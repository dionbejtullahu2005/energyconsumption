from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


WEATHER_HOURLY_FILE = "kosovo_district_weather_hourly.parquet"
WEATHER_DAILY_FILE = "kosovo_district_weather_daily.parquet"


def _joined_names(values: pd.Series) -> str | None:
    names = sorted({str(value) for value in values.dropna() if str(value).strip()})
    return "; ".join(names) if names else None


def _safe_corr(left: pd.Series, right: pd.Series) -> float:
    pair = pd.concat([left, right], axis=1).dropna()
    if len(pair) < 2 or pair.iloc[:, 0].nunique() <= 1 or pair.iloc[:, 1].nunique() <= 1:
        return np.nan
    return float(pair.iloc[:, 0].corr(pair.iloc[:, 1]))


def meter_prefix(value: Any) -> str | None:
    match = re.match(r"^([A-Za-z]+)", str(value).strip())
    return match.group(1).upper() if match else None


def district_mapping(config: dict[str, Any]) -> dict[str, str]:
    return {
        prefix.upper(): details["name"]
        for prefix, details in config["external_factors"]["districts"].items()
    }


def _holiday_calendar(holiday_csv_path: Path, config: dict[str, Any]) -> pd.DataFrame:
    events = pd.read_csv(holiday_csv_path)
    events["holiday_date"] = pd.to_datetime(events["holiday_date"]).dt.date
    events["observed_date"] = pd.to_datetime(events["observed_date"]).dt.date
    actual = events[["holiday_date", "holiday_name"]].rename(
        columns={"holiday_date": "date", "holiday_name": "actual_holiday_name"}
    )
    actual["is_official_holiday_date"] = True
    observed = events[["observed_date", "holiday_name"]].rename(
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
    calendar = pd.DataFrame({"date": pd.date_range(
        config["analysis"]["start_date"], config["analysis"]["end_date"], freq="D"
    ).date})
    calendar = calendar.merge(actual, on="date", how="left").merge(observed, on="date", how="left")
    for column in ("is_official_holiday_date", "is_observed_day_off"):
        calendar[column] = calendar[column].fillna(False).astype(bool)
    calendar["is_holiday_or_day_off"] = calendar["is_official_holiday_date"] | calendar["is_observed_day_off"]
    calendar["holiday_name"] = calendar[["actual_holiday_name", "observed_holiday_name"]].apply(
        lambda row: _joined_names(row), axis=1
    )
    calendar["is_day_before_holiday"] = calendar["is_holiday_or_day_off"].shift(-1, fill_value=False)
    calendar["is_day_after_holiday"] = calendar["is_holiday_or_day_off"].shift(1, fill_value=False)
    return calendar


def prepare_external_factors(
    raw_weather_path: Path, holiday_csv_path: Path, external_dir: Path,
    report_path: Path, config: dict[str, Any],
) -> dict[str, Any]:
    settings = config["external_factors"]
    raw = json.loads(raw_weather_path.read_text(encoding="utf-8"))
    source_payload = raw.get("districts", raw)
    sources = (
        {str(entry["meter_prefix"]).upper(): entry for entry in source_payload}
        if isinstance(source_payload, list) else source_payload
    )
    expected = {key.upper() for key in settings["districts"]}
    received = {key.upper() for key in sources}
    if expected != received:
        raise ValueError(f"Distriktet e motit nuk përputhen; mungojnë={sorted(expected-received)}, tepër={sorted(received-expected)}")

    hourly_parts: list[pd.DataFrame] = []
    daily_parts: list[pd.DataFrame] = []
    source_rows: list[dict[str, Any]] = []
    hdd_base = float(settings["hdd_base_c"])
    cdd_base = float(settings["cdd_base_c"])
    expected_hours = len(pd.date_range(
        f"{config['analysis']['start_date']} 00:00:00",
        f"{config['analysis']['end_date']} 23:00:00", freq="h"
    ))
    for prefix in sorted(expected):
        entry = sources[prefix] if prefix in sources else sources[prefix.lower()]
        response = entry.get("response", entry)
        district = settings["districts"][prefix]["name"]
        hourly = pd.DataFrame(response["hourly"]).rename(columns={"time": "interval_start"})
        daily = pd.DataFrame(response["daily"]).rename(columns={"time": "date"})
        hourly["interval_start"] = pd.to_datetime(hourly["interval_start"])
        hourly["date"] = hourly["interval_start"].dt.date
        daily["date"] = pd.to_datetime(daily["date"]).dt.date
        daily["hdd_18"] = (hdd_base - daily["temperature_2m_mean"]).clip(lower=0)
        daily["cdd_18"] = (daily["temperature_2m_mean"] - cdd_base).clip(lower=0)
        hourly = hourly.merge(daily[[
            "date", "temperature_2m_mean", "temperature_2m_min", "temperature_2m_max", "hdd_18", "cdd_18"
        ]], on="date", how="left", validate="many_to_one")
        hourly.insert(0, "district", district)
        hourly.insert(0, "meter_prefix", prefix)
        daily.insert(0, "district", district)
        daily.insert(0, "meter_prefix", prefix)
        if len(hourly) != expected_hours or hourly["interval_start"].duplicated().any():
            raise ValueError(f"Mbulimi orar i motit për {prefix}/{district} nuk është i plotë")
        if hourly["temperature_2m"].isna().any():
            raise ValueError(f"Temperatura që mungojnë për {prefix}/{district}")
        hourly_parts.append(hourly)
        daily_parts.append(daily)
        source_rows.append({
            "meter_prefix": prefix, "district": district,
            "requested_latitude": settings["districts"][prefix]["latitude"],
            "requested_longitude": settings["districts"][prefix]["longitude"],
            "returned_latitude": response.get("latitude"), "returned_longitude": response.get("longitude"),
            "elevation_m": response.get("elevation"), "timezone": response.get("timezone"),
            "hourly_rows": len(hourly), "daily_rows": len(daily),
        })
    hourly_all = pd.concat(hourly_parts, ignore_index=True)
    daily_all = pd.concat(daily_parts, ignore_index=True)
    calendar = _holiday_calendar(holiday_csv_path, config)
    external_dir.mkdir(parents=True, exist_ok=True)
    hourly_all.to_parquet(external_dir / WEATHER_HOURLY_FILE, index=False)
    daily_all.to_parquet(external_dir / WEATHER_DAILY_FILE, index=False)
    calendar.to_parquet(external_dir / "kosovo_holiday_calendar_daily.parquet", index=False)
    manifest = {
        "retrieved_at": raw.get("retrieved_at"),
        "weather_source": "Open-Meteo Historical Weather API",
        "weather_url": "https://archive-api.open-meteo.com/v1/archive",
        "temperature_assignment": "Temperatura lokale lidhet me prefiksin e njehsorit; kompanitë me disa distrikte përdorin pesha fikse sipas energjisë historike.",
        "districts": source_rows,
        "holiday_source": "Kalendari zyrtar i festave të Republikës së Kosovës",
    }
    (external_dir / "external_sources_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    summary = {
        "district_count": len(expected), "weather_hourly_rows": len(hourly_all),
        "weather_daily_rows": len(daily_all), "expected_hours_per_district": expected_hours,
        "temperature_min_c": float(hourly_all["temperature_2m"].min()),
        "temperature_max_c": float(hourly_all["temperature_2m"].max()),
        "holiday_calendar_days": len(calendar),
        "holiday_or_day_off_unique_days": int(calendar["is_holiday_or_day_off"].sum()),
        "hdd_base_c": hdd_base, "cdd_base_c": cdd_base,
    }
    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        pd.DataFrame({"metric": summary.keys(), "value": summary.values()}).to_excel(writer, sheet_name="Summary", index=False)
        pd.DataFrame(source_rows).to_excel(writer, sheet_name="District sources", index=False)
        daily_all.to_excel(writer, sheet_name="Daily weather", index=False)
        calendar.loc[calendar["is_holiday_or_day_off"]].to_excel(writer, sheet_name="Holidays in period", index=False)
    return summary


def enrich_hourly_consumption(
    long_path: Path, external_dir: Path, output_path: Path, config: dict[str, Any]
) -> dict[str, Any]:
    weather = pd.read_parquet(external_dir / WEATHER_HOURLY_FILE)
    holidays = pd.read_parquet(external_dir / "kosovo_holiday_calendar_daily.parquet")
    weather["interval_start"] = pd.to_datetime(weather["interval_start"])
    weather_columns = ["meter_prefix", "district", "interval_start", "temperature_2m",
        "temperature_2m_mean", "temperature_2m_min", "temperature_2m_max", "hdd_18", "cdd_18"]
    holiday_columns = ["date", "is_official_holiday_date", "is_observed_day_off",
        "is_holiday_or_day_off", "holiday_name", "is_day_before_holiday", "is_day_after_holiday"]
    known = set(district_mapping(config))
    parquet_file = pq.ParquetFile(long_path)
    temporary = output_path.with_suffix(".tmp.parquet")
    if temporary.exists():
        temporary.unlink()
    writer: pq.ParquetWriter | None = None
    row_count = missing_weather = 0
    energy_sum = 0.0
    unknown: set[str] = set()
    try:
        for batch in parquet_file.iter_batches(batch_size=200_000):
            frame = batch.to_pandas()
            frame["interval_start"] = pd.to_datetime(frame["interval_start"])
            frame["meter_prefix"] = frame["meter_id"].map(meter_prefix)
            unknown.update(set(frame["meter_prefix"].dropna()) - known)
            frame = frame.merge(weather[weather_columns], on=["meter_prefix", "interval_start"], how="left", validate="many_to_one")
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
    if unknown or row_count != parquet_file.metadata.num_rows or missing_weather:
        temporary.unlink(missing_ok=True)
        if unknown:
            raise ValueError(f"Prefikse të panjohura të njehsorëve: {sorted(unknown)}")
        if row_count != parquet_file.metadata.num_rows:
            raise ValueError(f"Rreshtat ndryshuan gjatë pasurimit: {row_count}")
        raise ValueError(f"Pas bashkimit mungojnë {missing_weather} temperatura")
    os.replace(temporary, output_path)
    return {"source_rows": parquet_file.metadata.num_rows, "enriched_rows": row_count,
        "district_count": len(known), "missing_weather_after_join": missing_weather,
        "enriched_energy_sum_kwh": energy_sum}


def _daily_energy_from_enriched(enriched_path: Path) -> pd.DataFrame:
    columns = ["company_id", "meter_id", "meter_prefix", "district", "energy_flow", "date", "energy_kwh",
        "temperature_2m_mean", "temperature_2m_min", "temperature_2m_max", "hdd_18", "cdd_18",
        "is_holiday_or_day_off", "holiday_name"]
    parts: list[pd.DataFrame] = []
    parquet_file = pq.ParquetFile(enriched_path)
    keys = ["company_id", "meter_id", "meter_prefix", "district", "date", "temperature_2m_mean",
        "temperature_2m_min", "temperature_2m_max", "hdd_18", "cdd_18", "is_holiday_or_day_off", "holiday_name"]
    for batch in parquet_file.iter_batches(columns=columns, batch_size=250_000):
        frame = batch.to_pandas()
        frame = frame.loc[frame["energy_flow"].eq("consumption_import")]
        parts.append(frame.groupby(keys, observed=True, dropna=False)["energy_kwh"].agg(
            energy_total_kwh="sum", valid_hours="count").reset_index())
    combined = pd.concat(parts, ignore_index=True)
    return combined.groupby(keys, observed=True, dropna=False).agg(
        energy_total_kwh=("energy_total_kwh", "sum"), valid_hours=("valid_hours", "sum")).reset_index()


def _company_district_membership(meter_daily: pd.DataFrame) -> pd.DataFrame:
    meter_energy = meter_daily.groupby(["company_id", "meter_id", "meter_prefix", "district"], observed=True).agg(
        historical_energy_kwh=("energy_total_kwh", "sum")).reset_index()
    district = meter_energy.groupby(["company_id", "meter_prefix", "district"], observed=True).agg(
        meter_count=("meter_id", "nunique"), historical_energy_kwh=("historical_energy_kwh", "sum")).reset_index()
    totals = district.groupby("company_id")["historical_energy_kwh"].transform("sum")
    counts = district.groupby("company_id")["district"].transform("nunique")
    district["fixed_district_weight"] = np.where(totals.gt(0), district["historical_energy_kwh"] / totals, 1 / counts)
    district["district_count"] = counts.astype(int)
    district["district_scope"] = np.where(counts.eq(1), district["district"], "Disa distrikte")
    return district


def _aggregate_company_daily(meter_daily: pd.DataFrame, membership: pd.DataFrame) -> pd.DataFrame:
    weights = membership[["company_id", "meter_prefix", "fixed_district_weight"]]
    frame = meter_daily.merge(weights, on=["company_id", "meter_prefix"], how="left", validate="many_to_one")
    valid = frame["valid_hours"].gt(0)
    frame["reporting_meter"] = frame["meter_id"].where(valid)
    for column in ("temperature_2m_mean", "temperature_2m_min", "temperature_2m_max", "hdd_18", "cdd_18"):
        frame[f"weighted_{column}"] = frame[column] * frame["fixed_district_weight"]
    company = frame.groupby(["company_id", "date", "is_holiday_or_day_off", "holiday_name"], observed=True, dropna=False).agg(
        energy_total_kwh=("energy_total_kwh", "sum"), valid_hours=("valid_hours", "sum"),
        reporting_meters=("reporting_meter", "nunique"), weight_present=("fixed_district_weight", "sum"),
        **{column: (f"weighted_{column}", "sum") for column in
           ("temperature_2m_mean", "temperature_2m_min", "temperature_2m_max", "hdd_18", "cdd_18")}
    ).reset_index()
    for column in ("temperature_2m_mean", "temperature_2m_min", "temperature_2m_max", "hdd_18", "cdd_18"):
        company[column] = company[column] / company["weight_present"]
    company = company.drop(columns="weight_present")
    metadata = membership.groupby("company_id", observed=True).agg(
        district_count=("district", "nunique"), districts=("district", _joined_names),
        district_scope=("district_scope", "first")).reset_index()
    company = company.merge(metadata, on="company_id", how="left", validate="many_to_one")
    company["temperature_method"] = np.where(company["district_count"].eq(1), "Temperaturë lokale e distriktit",
        "Mesatare lokale e ponderuar me energjinë historike")
    return company


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
        label = ("Lidhje statistikore me ditët e ftohta dhe të nxehta" if heating and cooling else
                 "Lidhje statistikore me ditët e ftohta" if heating else
                 "Lidhje statistikore me ditët e nxehta" if cooling else "Pa lidhje të qartë me motin")
        q25, q75 = valid["temperature_2m_mean"].quantile([0.25, 0.75])
        record = dict(zip(keys, identity_values))
        record.update({"days_with_consumption": len(valid), "temperature_correlation": temp_corr,
            "hdd_correlation": hdd_corr, "cdd_correlation": cdd_corr,
            "cold_days_mean_kwh": valid.loc[valid["temperature_2m_mean"].le(q25), "energy_total_kwh"].mean(),
            "hot_days_mean_kwh": valid.loc[valid["temperature_2m_mean"].ge(q75), "energy_total_kwh"].mean(),
            "weather_sensitivity_label": label, "weather_analysis_reliable": len(valid) >= minimum_days})
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
            baseline_values = normal.loc[normal["weekday_number"].eq(row.weekday_number), "energy_total_kwh"]
            baseline = baseline_values.mean()
            effect = (row.energy_total_kwh - baseline) / baseline if pd.notna(baseline) and baseline > 0 else np.nan
            records.append({"company_id": company, "date": row.date, "holiday_name": row.holiday_name,
                "weekday_number": row.weekday_number, "holiday_energy_kwh": row.energy_total_kwh,
                "same_weekday_baseline_kwh": baseline, "holiday_effect_pct": effect,
                "baseline_day_count": len(baseline_values)})
    detail = pd.DataFrame(records)
    summary = detail.groupby("company_id", observed=True).agg(
        holiday_days_analysed=("date", "nunique"), mean_holiday_effect_pct=("holiday_effect_pct", "mean"),
        median_holiday_effect_pct=("holiday_effect_pct", "median"),
        holidays_with_reduction=("holiday_effect_pct", lambda values: values.lt(0).sum())).reset_index()
    summary["holiday_behavior_label"] = np.select(
        [summary["mean_holiday_effect_pct"].le(-0.20), summary["mean_holiday_effect_pct"].ge(0.20)],
        ["Konsum zakonisht më i ulët në festa", "Konsum zakonisht më i lartë në festa"],
        default="Ndryshim i kufizuar në festa")
    return detail, summary


def _company_hourly_weather(company_outliers: pd.DataFrame, membership: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    company_times = company_outliers[["company_id", "interval_start"]].drop_duplicates()
    expanded = company_times.merge(membership[["company_id", "meter_prefix", "district", "fixed_district_weight",
        "district_count", "district_scope"]], on="company_id", how="left", validate="many_to_many")
    expanded = expanded.merge(weather[["meter_prefix", "interval_start", "temperature_2m", "temperature_2m_mean",
        "hdd_18", "cdd_18"]], on=["meter_prefix", "interval_start"], how="left", validate="many_to_one")
    for column in ("temperature_2m", "temperature_2m_mean", "hdd_18", "cdd_18"):
        expanded[column] *= expanded["fixed_district_weight"]
    return expanded.groupby(["company_id", "interval_start"], observed=True).agg(
        temperature_2m=("temperature_2m", "sum"), temperature_2m_mean=("temperature_2m_mean", "sum"),
        hdd_18=("hdd_18", "sum"), cdd_18=("cdd_18", "sum"), district_count=("district_count", "first"),
        district_scope=("district_scope", "first"), districts=("district", _joined_names)).reset_index()


def _prishtina_proxy_company_daily(
    company_daily: pd.DataFrame, district_daily_weather: pd.DataFrame
) -> pd.DataFrame:
    """Zëvendëson vetëm motin me serinë DPR; konsumi dhe festat mbeten të njëjta."""
    weather_columns = [
        "temperature_2m_mean", "temperature_2m_min", "temperature_2m_max", "hdd_18", "cdd_18"
    ]
    proxy = district_daily_weather.loc[
        district_daily_weather["meter_prefix"].eq("DPR"), ["date"] + weather_columns
    ].copy()
    base = company_daily.drop(
        columns=weather_columns + ["district_count", "districts", "district_scope", "temperature_method"],
        errors="ignore",
    )
    result = base.merge(proxy, on="date", how="left", validate="many_to_one")
    if result["temperature_2m_mean"].isna().any():
        raise ValueError("Mungon temperatura proxy e Prishtinës për një ose më shumë ditë")
    result["district_count"] = 1
    result["districts"] = "Prishtinë"
    result["district_scope"] = "Prishtinë (proxy)"
    result["temperature_method"] = "Temperatura e Prishtinës si proxy"
    return result


def _add_external_context(
    outliers: pd.DataFrame, cold_threshold: float, hot_threshold: float
) -> pd.DataFrame:
    result = outliers.copy()
    result["external_context"] = np.select([
        result["is_holiday_or_day_off"].fillna(False),
        result["temperature_2m"].le(cold_threshold), result["temperature_2m"].ge(hot_threshold)],
        ["Festë zyrtare ose ditë pushimi", "Temperaturë e ulët", "Temperaturë e lartë"],
        default="Pa kontekst të jashtëm të drejtpërdrejtë")
    return result


def analyze_external_factors(
    enriched_path: Path, processed_dir: Path, external_dir: Path, report_path: Path,
    outlier_report_path: Path, config: dict[str, Any],
) -> dict[str, Any]:
    meter_daily = _daily_energy_from_enriched(enriched_path)
    membership = _company_district_membership(meter_daily)
    company_daily = _aggregate_company_daily(meter_daily, membership)
    meter_sensitivity = _weather_sensitivity(meter_daily, ["company_id", "meter_id"], config)
    company_sensitivity = _weather_sensitivity(company_daily, ["company_id"], config).merge(
        company_daily[["company_id", "district_count", "districts", "district_scope", "temperature_method"]].drop_duplicates("company_id"),
        on="company_id", how="left", validate="one_to_one")
    district_daily_weather = pd.read_parquet(external_dir / WEATHER_DAILY_FILE)
    company_daily_proxy = _prishtina_proxy_company_daily(company_daily, district_daily_weather)
    company_sensitivity_proxy = _weather_sensitivity(
        company_daily_proxy, ["company_id"], config
    ).merge(
        company_daily_proxy[["company_id", "district_count", "districts", "district_scope", "temperature_method"]]
        .drop_duplicates("company_id"),
        on="company_id", how="left", validate="one_to_one",
    )
    meter_sensitivity = meter_sensitivity.merge(
        meter_daily[["company_id", "meter_id", "meter_prefix", "district"]].drop_duplicates(["company_id", "meter_id"]),
        on=["company_id", "meter_id"], how="left", validate="one_to_one")
    holiday_detail, holiday_summary = _holiday_effects(company_daily)
    company_outliers = pd.read_parquet(processed_dir / "company_hourly_outliers.parquet")
    weather = pd.read_parquet(external_dir / WEATHER_HOURLY_FILE)
    holidays = pd.read_parquet(external_dir / "kosovo_holiday_calendar_daily.parquet")
    local_weather = _company_hourly_weather(company_outliers, membership, weather)
    enriched_outliers = company_outliers.merge(local_weather, on=["company_id", "interval_start"], how="left", validate="one_to_one").merge(
        holidays[["date", "is_holiday_or_day_off", "holiday_name"]], on="date", how="left", validate="many_to_one")
    enriched_outliers["temperature_method"] = np.where(enriched_outliers["district_count"].eq(1),
        "Temperaturë lokale e distriktit", "Mesatare lokale e ponderuar me energjinë historike")
    cold_threshold = float(config["external_factors"]["cold_context_threshold_c"])
    hot_threshold = float(config["external_factors"]["hot_context_threshold_c"])
    enriched_outliers = _add_external_context(enriched_outliers, cold_threshold, hot_threshold)
    proxy_hourly = weather.loc[weather["meter_prefix"].eq("DPR"), [
        "interval_start", "temperature_2m", "temperature_2m_mean", "hdd_18", "cdd_18"
    ]]
    enriched_outliers_proxy = company_outliers.merge(
        proxy_hourly, on="interval_start", how="left", validate="many_to_one"
    ).merge(
        holidays[["date", "is_holiday_or_day_off", "holiday_name"]],
        on="date", how="left", validate="many_to_one",
    )
    enriched_outliers_proxy["district_count"] = 1
    enriched_outliers_proxy["district_scope"] = "Prishtinë (proxy)"
    enriched_outliers_proxy["districts"] = "Prishtinë"
    enriched_outliers_proxy["temperature_method"] = "Temperatura e Prishtinës si proxy"
    enriched_outliers_proxy = _add_external_context(
        enriched_outliers_proxy, cold_threshold, hot_threshold
    )
    processed_dir.mkdir(parents=True, exist_ok=True)
    outputs = {"meter_daily_energy_enriched.parquet": meter_daily,
        "company_daily_energy_enriched.parquet": company_daily,
        "company_daily_energy_prishtina_proxy.parquet": company_daily_proxy,
        "company_district_membership.parquet": membership,
        "meter_weather_sensitivity.parquet": meter_sensitivity, "company_weather_sensitivity.parquet": company_sensitivity,
        "company_weather_sensitivity_prishtina_proxy.parquet": company_sensitivity_proxy,
        "company_holiday_effect_detail.parquet": holiday_detail, "company_holiday_effect_summary.parquet": holiday_summary,
        "company_hourly_outliers_enriched.parquet": enriched_outliers,
        "company_hourly_outliers_prishtina_proxy.parquet": enriched_outliers_proxy}
    for name, frame in outputs.items():
        frame.to_parquet(processed_dir / name, index=False)
    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        company_sensitivity.to_excel(writer, sheet_name="Company weather", index=False)
        company_sensitivity_proxy.to_excel(writer, sheet_name="Company weather proxy", index=False)
        meter_sensitivity.to_excel(writer, sheet_name="Meter weather", index=False)
        membership.to_excel(writer, sheet_name="District membership", index=False)
        holiday_summary.to_excel(writer, sheet_name="Holiday summary", index=False)
        holiday_detail.to_excel(writer, sheet_name="Holiday detail", index=False)
    with pd.ExcelWriter(outlier_report_path, engine="openpyxl") as writer:
        enriched_outliers.to_excel(writer, sheet_name="Company outliers enriched", index=False)
        enriched_outliers_proxy.to_excel(writer, sheet_name="Outliers Prishtina proxy", index=False)
    return {"meter_daily_rows": len(meter_daily), "company_daily_rows": len(company_daily),
        "company_district_membership_rows": len(membership), "multi_district_companies": int(
            membership.loc[membership["district_count"].gt(1), "company_id"].nunique()),
        "meter_weather_profiles": len(meter_sensitivity), "company_weather_profiles": len(company_sensitivity),
        "company_weather_proxy_profiles": len(company_sensitivity_proxy),
        "reliable_company_weather_profiles": int(company_sensitivity["weather_analysis_reliable"].sum()),
        "holiday_company_event_rows": len(holiday_detail), "companies_with_holiday_analysis": len(holiday_summary),
        "company_outliers_enriched": len(enriched_outliers),
        "outliers_on_holiday_or_day_off": int(enriched_outliers["is_holiday_or_day_off"].fillna(False).sum()),
        "outliers_with_cold_context": int(enriched_outliers["temperature_2m"].le(cold_threshold).sum()),
        "outliers_with_hot_context": int(enriched_outliers["temperature_2m"].ge(hot_threshold).sum())}
