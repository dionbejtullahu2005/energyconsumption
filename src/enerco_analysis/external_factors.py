from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

def _joined_names(values: pd.Series) -> str | None:
    names = sorted({str(value) for value in values.dropna() if str(value).strip()})
    return "; ".join(names) if names else None


def _safe_corr(left: pd.Series, right: pd.Series) -> float:
    pair = pd.concat([left, right], axis=1).dropna()
    if len(pair) < 2 or pair.iloc[:, 0].nunique() <= 1 or pair.iloc[:, 1].nunique() <= 1:
        return np.nan
    return float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))


def prepare_external_factors(
    raw_weather_path: Path,
    holiday_csv_path: Path,
    external_dir: Path,
    report_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    settings = config["external_factors"]
    raw = json.loads(raw_weather_path.read_text(encoding="utf-8"))
    hourly = pd.DataFrame(raw["hourly"]).rename(columns={"time": "interval_start"})
    hourly["interval_start"] = pd.to_datetime(hourly["interval_start"])
    hourly["date"] = hourly["interval_start"].dt.date
    daily = pd.DataFrame(raw["daily"]).rename(columns={"time": "date"})
    daily["date"] = pd.to_datetime(daily["date"]).dt.date
    hdd_base = float(settings["hdd_base_c"])
    cdd_base = float(settings["cdd_base_c"])
    daily["hdd_18"] = (hdd_base - daily["temperature_2m_mean"]).clip(lower=0)
    daily["cdd_18"] = (daily["temperature_2m_mean"] - cdd_base).clip(lower=0)
    hourly = hourly.merge(
        daily[["date", "temperature_2m_mean", "temperature_2m_min", "temperature_2m_max", "hdd_18", "cdd_18"]],
        on="date",
        how="left",
        validate="many_to_one",
    )

    holiday_events = pd.read_csv(holiday_csv_path)
    holiday_events["holiday_date"] = pd.to_datetime(holiday_events["holiday_date"]).dt.date
    holiday_events["observed_date"] = pd.to_datetime(holiday_events["observed_date"]).dt.date
    actual = holiday_events[["holiday_date", "holiday_name"]].rename(
        columns={"holiday_date": "date", "holiday_name": "actual_holiday_name"}
    )
    actual["is_official_holiday_date"] = True
    observed = holiday_events[["observed_date", "holiday_name"]].rename(
        columns={"observed_date": "date", "holiday_name": "observed_holiday_name"}
    )
    observed["is_observed_day_off"] = True
    actual = actual.groupby("date", as_index=False).agg(
        is_official_holiday_date=("is_official_holiday_date", "max"),
        actual_holiday_name=("actual_holiday_name", _joined_names),
    )
    observed = observed.groupby("date", as_index=False).agg(
        is_observed_day_off=("is_observed_day_off", "max"),
        observed_holiday_name=("observed_holiday_name", _joined_names),
    )
    calendar = pd.DataFrame(
        {"date": pd.date_range(config["analysis"]["start_date"], config["analysis"]["end_date"], freq="D").date}
    )
    calendar = calendar.merge(actual, on="date", how="left").merge(observed, on="date", how="left")
    for column in ("is_official_holiday_date", "is_observed_day_off"):
        calendar[column] = calendar[column].fillna(False).astype(bool)
    calendar["is_holiday_or_day_off"] = (
        calendar["is_official_holiday_date"] | calendar["is_observed_day_off"]
    )
    calendar["holiday_name"] = calendar[["actual_holiday_name", "observed_holiday_name"]].apply(
        lambda row: _joined_names(row), axis=1
    )
    calendar["is_day_before_holiday"] = calendar["is_holiday_or_day_off"].shift(-1, fill_value=False)
    calendar["is_day_after_holiday"] = calendar["is_holiday_or_day_off"].shift(1, fill_value=False)

    expected_hours = len(pd.date_range(
        f"{config['analysis']['start_date']} 00:00:00",
        f"{config['analysis']['end_date']} 23:00:00",
        freq="h",
    ))
    if len(hourly) != expected_hours:
        raise ValueError(f"Mbulimi orar i motit nuk përputhet: {len(hourly)} != {expected_hours}")
    if hourly["interval_start"].duplicated().any():
        raise ValueError("Të dhënat e motit kanë timestamp-e të dubluara")
    if hourly["temperature_2m"].isna().any():
        raise ValueError("Të dhënat e motit kanë temperatura që mungojnë")

    external_dir.mkdir(parents=True, exist_ok=True)
    hourly.to_parquet(external_dir / "prishtina_weather_hourly.parquet", index=False)
    daily.to_parquet(external_dir / "prishtina_weather_daily.parquet", index=False)
    calendar.to_parquet(external_dir / "kosovo_holiday_calendar_daily.parquet", index=False)
    source_manifest = {
        "retrieved_on": "2026-08-17",
        "weather_source": "Open-Meteo Historical Weather API",
        "weather_url": "https://archive-api.open-meteo.com/v1/archive",
        "weather_requested_coordinates": [settings["latitude"], settings["longitude"]],
        "weather_returned_coordinates": [raw.get("latitude"), raw.get("longitude")],
        "weather_elevation_m": raw.get("elevation"),
        "weather_timezone": raw.get("timezone"),
        "holiday_source_2025": "https://bqk-kos.org/wp-content/uploads/2025/01/Kalendari-i-festave-2025.pdf",
        "holiday_source_2026": "https://bqk-kos.org/wp-content/uploads/2025/12/kalendari-i-festave-2026.pdf",
        "holiday_law": "Ligji nr. 03/L-064 për Festat Zyrtare në Republikën e Kosovës",
        "prishtina_proxy_limitation": "Temperatura e Prishtinës përdoret për të gjitha kompanitë pa lokacion anonim.",
    }
    (external_dir / "external_sources_manifest.json").write_text(
        json.dumps(source_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    summary = {
        "weather_hourly_rows": len(hourly),
        "weather_daily_rows": len(daily),
        "weather_missing_temperatures": int(hourly["temperature_2m"].isna().sum()),
        "weather_duplicate_timestamps": int(hourly["interval_start"].duplicated().sum()),
        "temperature_min_c": float(hourly["temperature_2m"].min()),
        "temperature_max_c": float(hourly["temperature_2m"].max()),
        "holiday_calendar_days": len(calendar),
        "official_holiday_dates_in_period": int(calendar["is_official_holiday_date"].sum()),
        "observed_days_off_in_period": int(calendar["is_observed_day_off"].sum()),
        "holiday_or_day_off_unique_days": int(calendar["is_holiday_or_day_off"].sum()),
        "hdd_base_c": hdd_base,
        "cdd_base_c": cdd_base,
    }
    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        pd.DataFrame({"metric": summary.keys(), "value": summary.values()}).to_excel(
            writer, sheet_name="Summary", index=False
        )
        daily.to_excel(writer, sheet_name="Daily weather", index=False)
        calendar.loc[calendar["is_holiday_or_day_off"]].to_excel(
            writer, sheet_name="Holidays in period", index=False
        )
        pd.DataFrame({"field": source_manifest.keys(), "value": source_manifest.values()}).to_excel(
            writer, sheet_name="Sources", index=False
        )
    return summary


def enrich_hourly_consumption(
    long_path: Path,
    external_dir: Path,
    output_path: Path,
) -> dict[str, Any]:
    weather = pd.read_parquet(external_dir / "prishtina_weather_hourly.parquet")
    holidays = pd.read_parquet(external_dir / "kosovo_holiday_calendar_daily.parquet")
    weather["interval_start"] = pd.to_datetime(weather["interval_start"])
    weather_columns = [
        "interval_start", "temperature_2m", "temperature_2m_mean", "temperature_2m_min",
        "temperature_2m_max", "hdd_18", "cdd_18"
    ]
    holiday_columns = [
        "date", "is_official_holiday_date", "is_observed_day_off", "is_holiday_or_day_off",
        "holiday_name", "is_day_before_holiday", "is_day_after_holiday"
    ]
    parquet_file = pq.ParquetFile(long_path)
    temporary = output_path.with_suffix(".tmp.parquet")
    if temporary.exists():
        temporary.unlink()
    writer: pq.ParquetWriter | None = None
    row_count = 0
    energy_sum = 0.0
    missing_weather = 0
    try:
        for batch in parquet_file.iter_batches(batch_size=200_000):
            frame = batch.to_pandas()
            frame["interval_start"] = pd.to_datetime(frame["interval_start"])
            frame = frame.merge(weather[weather_columns], on="interval_start", how="left", validate="many_to_one")
            frame = frame.merge(holidays[holiday_columns], on="date", how="left", validate="many_to_one")
            missing_weather += int(frame["temperature_2m"].isna().sum())
            energy_sum += float(frame["energy_kwh"].sum(skipna=True))
            table = pa.Table.from_pandas(frame, preserve_index=False)
            if writer is None:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                writer = pq.ParquetWriter(temporary, table.schema, compression="zstd", use_dictionary=True)
            writer.write_table(table)
            row_count += len(frame)
    finally:
        if writer is not None:
            writer.close()
    if row_count != parquet_file.metadata.num_rows:
        raise ValueError(f"Rreshtat ndryshuan gjatë pasurimit: {row_count}")
    if missing_weather:
        raise ValueError(f"Pas bashkimit mungojnë {missing_weather} temperatura")
    os.replace(temporary, output_path)
    return {
        "source_rows": parquet_file.metadata.num_rows,
        "enriched_rows": row_count,
        "missing_weather_after_join": missing_weather,
        "enriched_energy_sum_kwh": energy_sum,
    }


def _daily_energy_from_enriched(enriched_path: Path) -> pd.DataFrame:
    columns = [
        "company_id", "meter_id", "energy_flow", "date", "energy_kwh",
        "temperature_2m_mean", "temperature_2m_min", "temperature_2m_max", "hdd_18", "cdd_18",
        "is_holiday_or_day_off", "holiday_name"
    ]
    partials: list[pd.DataFrame] = []
    parquet_file = pq.ParquetFile(enriched_path)
    for batch in parquet_file.iter_batches(columns=columns, batch_size=250_000):
        frame = batch.to_pandas()
        frame = frame.loc[frame["energy_flow"].eq("consumption_import")]
        keys = [
            "company_id", "meter_id", "date", "temperature_2m_mean", "temperature_2m_min",
            "temperature_2m_max", "hdd_18", "cdd_18", "is_holiday_or_day_off", "holiday_name"
        ]
        partial = frame.groupby(keys, observed=True, dropna=False)["energy_kwh"].agg(
            energy_total_kwh="sum", valid_hours="count"
        ).reset_index()
        partials.append(partial)
    combined = pd.concat(partials, ignore_index=True)
    keys = [
        "company_id", "meter_id", "date", "temperature_2m_mean", "temperature_2m_min",
        "temperature_2m_max", "hdd_18", "cdd_18", "is_holiday_or_day_off", "holiday_name"
    ]
    return combined.groupby(keys, observed=True, dropna=False).agg(
        energy_total_kwh=("energy_total_kwh", "sum"), valid_hours=("valid_hours", "sum")
    ).reset_index()


def _weather_sensitivity(daily: pd.DataFrame, keys: list[str], config: dict[str, Any]) -> pd.DataFrame:
    threshold = float(config["external_factors"]["weather_correlation_threshold"])
    minimum_days = int(config["external_factors"]["minimum_days_for_weather_analysis"])
    records: list[dict[str, Any]] = []
    grouper: Any = keys[0] if len(keys) == 1 else keys
    for identity, group in daily.groupby(grouper, observed=True):
        identity_values = (identity,) if len(keys) == 1 else tuple(identity)
        valid = group.loc[group["valid_hours"].gt(0) & group["energy_total_kwh"].notna()]
        temp_corr = _safe_corr(valid["energy_total_kwh"], valid["temperature_2m_mean"])
        hdd_corr = _safe_corr(valid["energy_total_kwh"], valid["hdd_18"])
        cdd_corr = _safe_corr(valid["energy_total_kwh"], valid["cdd_18"])
        heating = pd.notna(hdd_corr) and hdd_corr >= threshold
        cooling = pd.notna(cdd_corr) and cdd_corr >= threshold
        if heating and cooling:
            label = "Lidhje statistikore me ditët e ftohta dhe të nxehta"
        elif heating:
            label = "Lidhje statistikore me ditët e ftohta"
        elif cooling:
            label = "Lidhje statistikore me ditët e nxehta"
        else:
            label = "Pa lidhje të qartë me motin"
        q25 = valid["temperature_2m_mean"].quantile(0.25)
        q75 = valid["temperature_2m_mean"].quantile(0.75)
        record = dict(zip(keys, identity_values))
        record.update(
            {
                "days_with_consumption": len(valid),
                "temperature_correlation": temp_corr,
                "hdd_correlation": hdd_corr,
                "cdd_correlation": cdd_corr,
                "cold_days_mean_kwh": valid.loc[valid["temperature_2m_mean"].le(q25), "energy_total_kwh"].mean(),
                "hot_days_mean_kwh": valid.loc[valid["temperature_2m_mean"].ge(q75), "energy_total_kwh"].mean(),
                "weather_sensitivity_label": label,
                "weather_analysis_reliable": len(valid) >= minimum_days,
            }
        )
        records.append(record)
    return pd.DataFrame(records)


def _holiday_effects(company_daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = company_daily.copy()
    frame["weekday_number"] = pd.to_datetime(frame["date"]).dt.weekday
    records: list[dict[str, Any]] = []
    for company, group in frame.groupby("company_id", observed=True):
        normal = group.loc[~group["is_holiday_or_day_off"] & group["valid_hours"].gt(0)]
        holidays = group.loc[group["is_holiday_or_day_off"] & group["valid_hours"].gt(0)]
        for row in holidays.itertuples(index=False):
            baseline_values = normal.loc[
                normal["weekday_number"].eq(row.weekday_number), "energy_total_kwh"
            ]
            baseline = baseline_values.mean()
            effect = (row.energy_total_kwh - baseline) / baseline if pd.notna(baseline) and baseline > 0 else np.nan
            records.append(
                {
                    "company_id": company,
                    "date": row.date,
                    "holiday_name": row.holiday_name,
                    "weekday_number": row.weekday_number,
                    "holiday_energy_kwh": row.energy_total_kwh,
                    "same_weekday_baseline_kwh": baseline,
                    "holiday_effect_pct": effect,
                    "baseline_day_count": len(baseline_values),
                }
            )
    detail = pd.DataFrame(records)
    summary = detail.groupby("company_id", observed=True).agg(
        holiday_days_analysed=("date", "nunique"),
        mean_holiday_effect_pct=("holiday_effect_pct", "mean"),
        median_holiday_effect_pct=("holiday_effect_pct", "median"),
        holidays_with_reduction=("holiday_effect_pct", lambda values: values.lt(0).sum()),
    ).reset_index()
    summary["holiday_behavior_label"] = np.select(
        [summary["mean_holiday_effect_pct"].le(-0.20), summary["mean_holiday_effect_pct"].ge(0.20)],
        ["Konsum zakonisht më i ulët në festa", "Konsum zakonisht më i lartë në festa"],
        default="Ndryshim i kufizuar në festa",
    )
    return detail, summary


def analyze_external_factors(
    enriched_path: Path,
    processed_dir: Path,
    external_dir: Path,
    report_path: Path,
    outlier_report_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    meter_daily = _daily_energy_from_enriched(enriched_path)
    company_keys = [
        "company_id", "date", "temperature_2m_mean", "temperature_2m_min", "temperature_2m_max",
        "hdd_18", "cdd_18", "is_holiday_or_day_off", "holiday_name"
    ]
    company_daily = meter_daily.groupby(company_keys, observed=True, dropna=False).agg(
        energy_total_kwh=("energy_total_kwh", "sum"), valid_hours=("valid_hours", "sum"),
        reporting_meters=("meter_id", "nunique")
    ).reset_index()
    meter_sensitivity = _weather_sensitivity(meter_daily, ["company_id", "meter_id"], config)
    company_sensitivity = _weather_sensitivity(company_daily, ["company_id"], config)
    holiday_detail, holiday_summary = _holiday_effects(company_daily)

    company_outliers = pd.read_parquet(processed_dir / "company_hourly_outliers.parquet")
    weather_hourly = pd.read_parquet(external_dir / "prishtina_weather_hourly.parquet")
    holidays = pd.read_parquet(external_dir / "kosovo_holiday_calendar_daily.parquet")
    enriched_outliers = company_outliers.merge(
        weather_hourly[["interval_start", "temperature_2m", "temperature_2m_mean", "hdd_18", "cdd_18"]],
        on="interval_start", how="left", validate="many_to_one"
    ).merge(
        holidays[["date", "is_holiday_or_day_off", "holiday_name"]],
        on="date", how="left", validate="many_to_one"
    )
    cold_threshold = float(config["external_factors"]["cold_context_threshold_c"])
    hot_threshold = float(config["external_factors"]["hot_context_threshold_c"])
    enriched_outliers["external_context"] = np.select(
        [
            enriched_outliers["is_holiday_or_day_off"].fillna(False),
            enriched_outliers["temperature_2m"].le(cold_threshold),
            enriched_outliers["temperature_2m"].ge(hot_threshold),
        ],
        ["Festë zyrtare ose ditë pushimi", "Temperaturë e ulët", "Temperaturë e lartë"],
        default="Pa kontekst të jashtëm të drejtpërdrejtë",
    )

    processed_dir.mkdir(parents=True, exist_ok=True)
    meter_daily.to_parquet(processed_dir / "meter_daily_energy_enriched.parquet", index=False)
    company_daily.to_parquet(processed_dir / "company_daily_energy_enriched.parquet", index=False)
    meter_sensitivity.to_parquet(processed_dir / "meter_weather_sensitivity.parquet", index=False)
    company_sensitivity.to_parquet(processed_dir / "company_weather_sensitivity.parquet", index=False)
    holiday_detail.to_parquet(processed_dir / "company_holiday_effect_detail.parquet", index=False)
    holiday_summary.to_parquet(processed_dir / "company_holiday_effect_summary.parquet", index=False)
    enriched_outliers.to_parquet(processed_dir / "company_hourly_outliers_enriched.parquet", index=False)

    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        company_sensitivity.to_excel(writer, sheet_name="Company weather", index=False)
        meter_sensitivity.to_excel(writer, sheet_name="Meter weather", index=False)
        holiday_summary.to_excel(writer, sheet_name="Holiday summary", index=False)
        holiday_detail.to_excel(writer, sheet_name="Holiday detail", index=False)
    with pd.ExcelWriter(outlier_report_path, engine="openpyxl") as writer:
        enriched_outliers.to_excel(writer, sheet_name="Company outliers enriched", index=False)

    summary = {
        "meter_daily_rows": len(meter_daily),
        "company_daily_rows": len(company_daily),
        "meter_weather_profiles": len(meter_sensitivity),
        "company_weather_profiles": len(company_sensitivity),
        "reliable_company_weather_profiles": int(company_sensitivity["weather_analysis_reliable"].sum()),
        "holiday_company_event_rows": len(holiday_detail),
        "companies_with_holiday_analysis": len(holiday_summary),
        "company_outliers_enriched": len(enriched_outliers),
        "outliers_on_holiday_or_day_off": int(enriched_outliers["is_holiday_or_day_off"].fillna(False).sum()),
        "outliers_with_cold_context": int(enriched_outliers["temperature_2m"].le(cold_threshold).sum()),
        "outliers_with_hot_context": int(enriched_outliers["temperature_2m"].ge(hot_threshold).sum()),
    }
    return summary
