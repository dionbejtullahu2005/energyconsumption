from __future__ import annotations
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

IDENTITY_COLUMNS = ["company_id", "meter_id", "energy_flow"]
VALUE_COLUMNS = [
    "company_id",
    "meter_id",
    "energy_flow",
    "interval_start",
    "date",
    "hour_1_24",
    "is_weekend",
    "is_peak_07_18",
    "energy_kwh",
    "quality_status",
    "active_period_quality_status",
    "source_sheet",
]


def _safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    result = numerator / denominator
    return result.where(denominator > 0)

def _basic_metrics(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    grouped = frame.groupby(keys, observed=True, dropna=False)["energy_kwh"]
    base = grouped.agg(
        expected_rows="size",
        valid_hours="count",
        energy_total_kwh="sum",
        energy_mean_kwh="mean",
        energy_std_kwh="std",
        energy_max_kwh="max",
    )
    base["missing_hours"] = base["expected_rows"] - base["valid_hours"]
    base["coverage_share"] = base["valid_hours"] / base["expected_rows"]

    peak = (
        frame.loc[frame["is_peak_07_18"]]
        .groupby(keys, observed=True, dropna=False)["energy_kwh"]
        .mean()
    )
    offpeak = (
        frame.loc[~frame["is_peak_07_18"]]
        .groupby(keys, observed=True, dropna=False)["energy_kwh"]
        .mean()
    )
    weekday = (
        frame.loc[~frame["is_weekend"]]
        .groupby(keys, observed=True, dropna=False)["energy_kwh"]
        .mean()
    )
    weekend = (
        frame.loc[frame["is_weekend"]]
        .groupby(keys, observed=True, dropna=False)["energy_kwh"]
        .mean()
    )
    base["peak_mean_kwh"] = peak
    base["offpeak_mean_kwh"] = offpeak
    base["peak_offpeak_ratio"] = _safe_ratio(peak, offpeak)
    base["weekday_mean_kwh"] = weekday
    base["weekend_mean_kwh"] = weekend
    base["weekday_weekend_ratio"] = _safe_ratio(weekday, weekend)
    base["coefficient_of_variation"] = _safe_ratio(
        base["energy_std_kwh"], base["energy_mean_kwh"]
    )
    base["load_factor"] = _safe_ratio(
        base["energy_mean_kwh"], base["energy_max_kwh"]
    )
    base["zero_offpeak_denominator"] = offpeak.fillna(0).eq(0)
    base["zero_weekend_denominator"] = weekend.fillna(0).eq(0)
    return base.reset_index()


def _monthly_profiles(
    frame: pd.DataFrame,
    keys: list[str],
    profile_start: pd.Timestamp,
    profile_end: pd.Timestamp,
) -> pd.DataFrame:
    selected = frame.loc[
        frame["interval_start"].between(profile_start, profile_end + pd.Timedelta(hours=23))
    ].copy()
    selected["month_start"] = selected["interval_start"].dt.to_period("M").dt.to_timestamp()
    monthly = (
        selected.groupby(keys + ["month_start"], observed=True, dropna=False)["energy_kwh"]
        .agg(month_total_kwh="sum", month_mean_kwh="mean", valid_hours="count", expected_hours="size")
        .reset_index()
    )
    monthly["coverage_share"] = monthly["valid_hours"] / monthly["expected_hours"]
    annual_mean = selected.groupby(keys, observed=True, dropna=False)["energy_kwh"].mean()
    monthly = monthly.join(annual_mean.rename("profile_period_mean_kwh"), on=keys)
    monthly["seasonality_index"] = monthly["month_mean_kwh"] / monthly["profile_period_mean_kwh"]
    return monthly

def _seasonality_and_trend(
    monthly: pd.DataFrame,
    keys: list[str],
    config: dict[str, Any],
) -> pd.DataFrame:
    index_threshold = float(config["profile_metrics"]["seasonality_index_threshold"])
    difference_threshold = float(config["profile_metrics"]["seasonality_difference_threshold"])
    first_months = sorted(monthly["month_start"].dropna().unique())[:3]
    last_month = monthly["month_start"].max()
    records: list[dict[str, Any]] = []
    grouper: Any = keys[0] if len(keys) == 1 else keys
    for identity, group in monthly.groupby(grouper, observed=True, dropna=False):
        identity_values = (identity,) if len(keys) == 1 else tuple(identity)
        record = dict(zip(keys, identity_values))
        summer = group.loc[group["month_start"].dt.month.isin([6, 7, 8]), "seasonality_index"].mean()
        winter = group.loc[group["month_start"].dt.month.isin([12, 1, 2]), "seasonality_index"].mean()
        first_three = group.loc[group["month_start"].isin(first_months), "month_mean_kwh"].mean()
        last_value = group.loc[group["month_start"].eq(last_month), "month_mean_kwh"].mean()
        first_three_rows = group.loc[group["month_start"].isin(first_months)]
        last_rows = group.loc[group["month_start"].eq(last_month)]
        reliable_months = int(group.loc[group["coverage_share"] >= 0.90, "month_start"].nunique())
        if pd.notna(summer) and summer >= index_threshold and summer - winter >= difference_threshold:
            label = "Verë"
        elif pd.notna(winter) and winter >= index_threshold and winter - summer >= difference_threshold:
            label = "Dimër"
        else:
            label = "Asnjë"
        record.update(
            {
                "summer_index": summer,
                "winter_index": winter,
                "seasonality_label": label,
                "first_3_months_mean_kwh": first_three,
                "last_month_mean_kwh": last_value,
                "monthly_trend_pct": (
                    (last_value - first_three) / first_three
                    if pd.notna(first_three) and first_three > 0
                    else np.nan
                ),
                "months_with_values": int(group.loc[group["valid_hours"] > 0, "month_start"].nunique()),
                "months_with_90pct_coverage": reliable_months,
                "seasonality_reliable": reliable_months == 12,
                "trend_reliable": (
                    len(first_three_rows) == 3
                    and first_three_rows["coverage_share"].ge(0.90).all()
                    and len(last_rows) == 1
                    and last_rows["coverage_share"].ge(0.90).all()
                ),
            }
        )
        records.append(record)
    return pd.DataFrame(records)


def _hourly_profiles(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    return (
        frame.groupby(keys + ["hour_1_24"], observed=True, dropna=False)["energy_kwh"]
        .agg(mean_kwh="mean", valid_hours="count")
        .reset_index()
    )


def _meter_heterogeneity(
    meter_metrics: pd.DataFrame,
    hourly_profiles: pd.DataFrame,
    config: dict[str, Any],
) -> pd.DataFrame:
    metrics = meter_metrics.loc[meter_metrics["energy_flow"].eq("consumption_import")]
    profiles = hourly_profiles.loc[hourly_profiles["energy_flow"].eq("consumption_import")]
    similar_threshold = float(config["profile_metrics"]["similar_profile_correlation"])
    partial_threshold = float(config["profile_metrics"]["partly_different_profile_correlation"])
    records: list[dict[str, Any]] = []
    for company, company_metrics in metrics.groupby("company_id", observed=True):
        meter_ids = company_metrics["meter_id"].tolist()
        if len(meter_ids) == 1:
            records.append(
                {
                    "company_id": company,
                    "meter_count": 1,
                    "mean_pairwise_profile_correlation": np.nan,
                    "minimum_pairwise_profile_correlation": np.nan,
                    "peak_ratio_range": 0.0,
                    "load_factor_range": 0.0,
                    "meter_profile_similarity": "Një njehsor",
                }
            )
            continue
        pivot = (
            profiles.loc[profiles["company_id"].eq(company)]
            .pivot(index="hour_1_24", columns="meter_id", values="mean_kwh")
            .reindex(range(1, 25))
        )
        correlations: list[float] = []
        for left, right in combinations(meter_ids, 2):
            pair = pivot[[left, right]].dropna()
            if len(pair) < 6:
                correlation = np.nan
            else:
                left_constant = pair[left].nunique() <= 1
                right_constant = pair[right].nunique() <= 1
                if left_constant and right_constant:
                    correlation = 1.0
                elif left_constant or right_constant:
                    correlation = 0.0
                else:
                    correlation = pair[left].corr(pair[right])
            if pd.notna(correlation):
                correlations.append(float(correlation))
        mean_correlation = float(np.mean(correlations)) if correlations else np.nan
        minimum_correlation = float(np.min(correlations)) if correlations else np.nan
        if pd.notna(minimum_correlation) and minimum_correlation >= similar_threshold:
            label = "Profile të ngjashme"
        elif pd.notna(mean_correlation) and mean_correlation >= partial_threshold:
            label = "Profile pjesërisht të ndryshme"
        else:
            label = "Profile dukshëm të ndryshme"
        records.append(
            {
                "company_id": company,
                "meter_count": len(meter_ids),
                "mean_pairwise_profile_correlation": mean_correlation,
                "minimum_pairwise_profile_correlation": minimum_correlation,
                "peak_ratio_range": company_metrics["peak_offpeak_ratio"].max()
                - company_metrics["peak_offpeak_ratio"].min(),
                "load_factor_range": company_metrics["load_factor"].max()
                - company_metrics["load_factor"].min(),
                "meter_profile_similarity": label,
            }
        )
    return pd.DataFrame(records)


def build_profile_metrics(
    long_path: Path,
    processed_dir: Path,
    report_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    parquet_file = pq.ParquetFile(long_path)
    sheet_names: set[str] = set()
    for batch in parquet_file.iter_batches(columns=["source_sheet"], batch_size=250_000):
        sheet_names.update(batch.column(0).to_pylist())

    meter_frames: list[pd.DataFrame] = []
    meter_monthly_frames: list[pd.DataFrame] = []
    meter_hourly_frames: list[pd.DataFrame] = []
    consumption_frames: list[pd.DataFrame] = []
    profile_start = pd.Timestamp(config["analysis"]["profile_start_date"])
    profile_end = pd.Timestamp(config["analysis"]["profile_end_date"])

    for sheet_name in sorted(sheet_names):
        table = pq.read_table(long_path, columns=VALUE_COLUMNS, filters=[("source_sheet", "=", sheet_name)])
        frame = table.to_pandas()
        frame["interval_start"] = pd.to_datetime(frame["interval_start"])
        meter_base = _basic_metrics(frame, IDENTITY_COLUMNS)
        quality_columns = [
            "company_id", "meter_id", "energy_flow", "quality_status",
            "active_period_quality_status", "source_sheet"
        ]
        quality = frame[quality_columns].drop_duplicates(IDENTITY_COLUMNS)
        meter_base = meter_base.merge(quality, on=IDENTITY_COLUMNS, how="left")
        monthly = _monthly_profiles(frame, IDENTITY_COLUMNS, profile_start, profile_end)
        seasonal = _seasonality_and_trend(monthly, IDENTITY_COLUMNS, config)
        meter_frames.append(meter_base.merge(seasonal, on=IDENTITY_COLUMNS, how="left"))
        meter_monthly_frames.append(monthly)
        meter_hourly_frames.append(_hourly_profiles(frame, IDENTITY_COLUMNS))
        consumption_frames.append(
            frame.loc[frame["energy_flow"].eq("consumption_import"), [
                "company_id", "interval_start", "date", "hour_1_24", "is_weekend",
                "is_peak_07_18", "energy_kwh"
            ]]
        )

    meter_metrics = pd.concat(meter_frames, ignore_index=True)
    meter_monthly = pd.concat(meter_monthly_frames, ignore_index=True)
    meter_hourly = pd.concat(meter_hourly_frames, ignore_index=True)

    consumption = pd.concat(consumption_frames, ignore_index=True)
    time_keys = ["company_id", "interval_start", "date", "hour_1_24", "is_weekend", "is_peak_07_18"]
    company_hourly = (
        consumption.groupby(time_keys, observed=True, dropna=False)["energy_kwh"]
        .agg(["sum", "count"])
        .reset_index()
        .rename(columns={"sum": "energy_kwh", "count": "reporting_meter_count"})
    )
    company_hourly.loc[company_hourly["reporting_meter_count"].eq(0), "energy_kwh"] = np.nan
    company_metrics = _basic_metrics(company_hourly, ["company_id"])
    company_monthly = _monthly_profiles(company_hourly, ["company_id"], profile_start, profile_end)
    company_seasonal = _seasonality_and_trend(company_monthly, ["company_id"], config)
    company_metrics = company_metrics.merge(company_seasonal, on="company_id", how="left")
    meter_counts = (
        meter_metrics.loc[meter_metrics["energy_flow"].eq("consumption_import")]
        .groupby("company_id")["meter_id"]
        .nunique()
        .rename("meter_count")
    )
    reporting = company_hourly.groupby("company_id")["reporting_meter_count"].agg(
        mean_reporting_meters="mean", minimum_reporting_meters="min", maximum_reporting_meters="max"
    )
    company_metrics = company_metrics.join(meter_counts, on="company_id").join(reporting, on="company_id")
    company_hourly_profile = _hourly_profiles(company_hourly, ["company_id"])
    heterogeneity = _meter_heterogeneity(meter_metrics, meter_hourly, config)

    processed_dir.mkdir(parents=True, exist_ok=True)
    meter_metrics.to_parquet(processed_dir / "meter_profile_metrics.parquet", index=False)
    company_metrics.to_parquet(processed_dir / "company_profile_metrics.parquet", index=False)
    meter_monthly.to_parquet(processed_dir / "meter_monthly_profiles.parquet", index=False)
    company_monthly.to_parquet(processed_dir / "company_monthly_profiles.parquet", index=False)
    meter_hourly.to_parquet(processed_dir / "meter_hourly_profiles.parquet", index=False)
    company_hourly_profile.to_parquet(processed_dir / "company_hourly_profiles.parquet", index=False)
    heterogeneity.to_parquet(processed_dir / "company_meter_heterogeneity.parquet", index=False)

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        meter_metrics.to_excel(writer, sheet_name="Meter metrics", index=False)
        company_metrics.to_excel(writer, sheet_name="Company metrics", index=False)
        heterogeneity.to_excel(writer, sheet_name="Meter heterogeneity", index=False)

    return {
        "meter_energy_series": len(meter_metrics),
        "physical_meters": int(meter_metrics["meter_id"].nunique()),
        "companies": len(company_metrics),
        "profile_months": int(company_monthly["month_start"].nunique()),
        "companies_one_meter": int(heterogeneity["meter_profile_similarity"].eq("Një njehsor").sum()),
        "companies_similar_meters": int(
            heterogeneity["meter_profile_similarity"].eq("Profile të ngjashme").sum()
        ),
        "companies_partly_different_meters": int(
            heterogeneity["meter_profile_similarity"].eq("Profile pjesërisht të ndryshme").sum()
        ),
        "companies_different_meters": int(
            heterogeneity["meter_profile_similarity"].eq("Profile dukshëm të ndryshme").sum()
        ),
    }
