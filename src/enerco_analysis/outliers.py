from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


METER_KEYS = ["company_id", "meter_id", "energy_flow"]
COMPANY_METRICS_FOR_PEERS = [
    "peak_offpeak_ratio",
    "weekday_weekend_ratio",
    "coefficient_of_variation",
    "load_factor",
    "summer_index",
    "winter_index",
    "monthly_trend_pct",
]


def _zscore_outliers(
    frame: pd.DataFrame,
    metrics: pd.DataFrame,
    keys: list[str],
    threshold: float,
    extreme_multiple: float,
) -> pd.DataFrame:
    reference = metrics[keys + ["energy_mean_kwh", "energy_std_kwh"]].copy()
    merged = frame.merge(reference, on=keys, how="left", validate="many_to_one")
    valid_std = merged["energy_std_kwh"].gt(0) & merged["energy_kwh"].notna()
    merged["z_score"] = np.nan
    merged.loc[valid_std, "z_score"] = (
        merged.loc[valid_std, "energy_kwh"] - merged.loc[valid_std, "energy_mean_kwh"]
    ) / merged.loc[valid_std, "energy_std_kwh"]
    result = merged.loc[merged["z_score"].abs().gt(threshold)].copy()
    result["outlier_direction"] = np.where(result["z_score"].gt(0), "I lartë", "I ulët")
    result["reason"] = np.where(
        result["z_score"].gt(0),
        f"Konsum mbi mesataren me Z > {threshold:g}",
        f"Konsum nën mesataren me Z < -{threshold:g}",
    )
    technical_high = result["energy_kwh"].gt(extreme_multiple * result["energy_mean_kwh"])
    result["recommendation"] = np.select(
        [result["z_score"].lt(-threshold), technical_high],
        ["Për shqyrtim teknik ose ndërprerje", "Për shqyrtim teknik"],
        default="Sjellje potencialisht legjitime – verifikim biznesi",
    )
    return result

def _company_hourly_from_long(long_path: Path) -> pd.DataFrame:
    columns = [
        "company_id", "energy_flow", "interval_start", "date", "hour_1_24",
        "is_weekend", "is_peak_07_18", "energy_kwh", "source_sheet"
    ]
    parquet_file = pq.ParquetFile(long_path)
    sheets: set[str] = set()
    for batch in parquet_file.iter_batches(columns=["source_sheet"], batch_size=250_000):
        sheets.update(batch.column(0).to_pylist())
    consumption_frames: list[pd.DataFrame] = []
    for sheet in sorted(sheets):
        frame = pq.read_table(long_path, columns=columns, filters=[("source_sheet", "=", sheet)]).to_pandas()
        consumption_frames.append(
            frame.loc[frame["energy_flow"].eq("consumption_import")].drop(
                columns=["energy_flow", "source_sheet"]
            )
        )
    consumption = pd.concat(consumption_frames, ignore_index=True)
    time_keys = ["company_id", "interval_start", "date", "hour_1_24", "is_weekend", "is_peak_07_18"]
    company_hourly = (
        consumption.groupby(time_keys, observed=True, dropna=False)["energy_kwh"]
        .agg(["sum", "count"])
        .reset_index()
        .rename(columns={"sum": "energy_kwh", "count": "reporting_meter_count"})
    )
    company_hourly.loc[company_hourly["reporting_meter_count"].eq(0), "energy_kwh"] = np.nan
    return company_hourly


def _global_company_metric_outliers(
    company_metrics: pd.DataFrame,
    threshold: float,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for metric in COMPANY_METRICS_FOR_PEERS:
        values = company_metrics[["company_id", metric]].dropna()
        mean = values[metric].mean()
        std = values[metric].std()
        if pd.isna(std) or std <= 0:
            continue
        values = values.assign(z_score=(values[metric] - mean) / std)
        for row in values.loc[values["z_score"].abs().gt(threshold)].itertuples(index=False):
            records.append(
                {
                    "company_id": row.company_id,
                    "metric": metric,
                    "metric_value": getattr(row, metric),
                    "peer_mean": mean,
                    "peer_std": std,
                    "z_score": row.z_score,
                    "comparison_scope": "Të gjitha kompanitë – provizore",
                    "sector_comparison_status": "Në pritje të metadata-s së sektorit",
                    "recommendation": "Verifikim biznesi; mos interpretohet si outlier sektorial",
                }
            )
    return pd.DataFrame(records)


def build_outlier_report(
    long_path: Path,
    processed_dir: Path,
    report_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    meter_metrics = pd.read_parquet(processed_dir / "meter_profile_metrics.parquet")
    company_metrics = pd.read_parquet(processed_dir / "company_profile_metrics.parquet")
    hourly_threshold = float(config["outliers"]["hourly_zscore_threshold"])
    peer_threshold = float(config["outliers"]["sector_zscore_threshold"])
    extreme_multiple = float(config["quality"]["extreme_multiple_of_mean"])

    meter_outlier_frames: list[pd.DataFrame] = []
    parquet_file = pq.ParquetFile(long_path)
    sheets: set[str] = set()
    for batch in parquet_file.iter_batches(columns=["source_sheet"], batch_size=250_000):
        sheets.update(batch.column(0).to_pylist())
    columns = METER_KEYS + [
        "interval_start", "date", "hour_1_24", "energy_kwh", "quality_status",
        "active_period_quality_status", "source_sheet"
    ]
    for sheet in sorted(sheets):
        frame = pq.read_table(long_path, columns=columns, filters=[("source_sheet", "=", sheet)]).to_pandas()
        sheet_metrics = meter_metrics.loc[meter_metrics["source_sheet"].eq(sheet)]
        meter_outlier_frames.append(
            _zscore_outliers(
                frame,
                sheet_metrics,
                METER_KEYS,
                hourly_threshold,
                extreme_multiple,
            )
        )
    meter_outliers = pd.concat(meter_outlier_frames, ignore_index=True)

    company_hourly = _company_hourly_from_long(long_path)
    company_outliers = _zscore_outliers(
        company_hourly,
        company_metrics,
        ["company_id"],
        hourly_threshold,
        extreme_multiple,
    )
    global_metric_outliers = _global_company_metric_outliers(company_metrics, peer_threshold)

    meter_columns = [
        "company_id", "meter_id", "energy_flow", "interval_start", "date", "hour_1_24",
        "energy_kwh", "energy_mean_kwh", "energy_std_kwh", "z_score", "outlier_direction",
        "quality_status", "active_period_quality_status", "source_sheet", "reason", "recommendation"
    ]
    company_columns = [
        "company_id", "interval_start", "date", "hour_1_24", "energy_kwh",
        "reporting_meter_count", "energy_mean_kwh", "energy_std_kwh", "z_score",
        "outlier_direction", "reason", "recommendation"
    ]
    meter_outliers = meter_outliers[meter_columns].sort_values("z_score", key=lambda s: s.abs(), ascending=False)
    company_outliers = company_outliers[company_columns].sort_values(
        "z_score", key=lambda s: s.abs(), ascending=False
    )
    if not global_metric_outliers.empty:
        global_metric_outliers = global_metric_outliers.sort_values(
            "z_score", key=lambda s: s.abs(), ascending=False
        )

    processed_dir.mkdir(parents=True, exist_ok=True)
    meter_outliers.to_parquet(processed_dir / "meter_hourly_outliers.parquet", index=False)
    company_outliers.to_parquet(processed_dir / "company_hourly_outliers.parquet", index=False)
    global_metric_outliers.to_parquet(
        processed_dir / "company_metric_outliers_global_provisional.parquet", index=False
    )

    meter_summary = (
        meter_outliers.groupby(METER_KEYS, observed=True)
        .agg(
            outlier_hours=("z_score", "size"),
            maximum_absolute_z=("z_score", lambda values: values.abs().max()),
            high_outliers=("outlier_direction", lambda values: values.eq("I lartë").sum()),
            low_outliers=("outlier_direction", lambda values: values.eq("I ulët").sum()),
        )
        .reset_index()
        .sort_values("outlier_hours", ascending=False)
    )
    company_summary = (
        company_outliers.groupby("company_id", observed=True)
        .agg(
            outlier_hours=("z_score", "size"),
            maximum_absolute_z=("z_score", lambda values: values.abs().max()),
            high_outliers=("outlier_direction", lambda values: values.eq("I lartë").sum()),
            low_outliers=("outlier_direction", lambda values: values.eq("I ulët").sum()),
        )
        .reset_index()
        .sort_values("outlier_hours", ascending=False)
    )

    excel_limit = 100_000
    summary = {
        "hourly_zscore_threshold": hourly_threshold,
        "peer_metric_zscore_threshold": peer_threshold,
        "meter_hourly_outliers": len(meter_outliers),
        "meter_series_with_outliers": len(meter_summary),
        "company_hourly_outliers": len(company_outliers),
        "companies_with_hourly_outliers": len(company_summary),
        "global_provisional_metric_outliers": len(global_metric_outliers),
        "sector_outliers_completed": False,
        "sector_outliers_blocker": "Mungon metadata e sektorit",
        "excel_detail_row_limit": excel_limit,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        pd.DataFrame({"metric": summary.keys(), "value": summary.values()}).to_excel(
            writer, sheet_name="Summary", index=False
        )
        meter_summary.to_excel(writer, sheet_name="Meter summary", index=False)
        company_summary.to_excel(writer, sheet_name="Company summary", index=False)
        meter_outliers.head(excel_limit).to_excel(writer, sheet_name="Meter hourly detail", index=False)
        company_outliers.head(excel_limit).to_excel(writer, sheet_name="Company hourly detail", index=False)
        global_metric_outliers.to_excel(writer, sheet_name="Global metric provisional", index=False)
    return summary

