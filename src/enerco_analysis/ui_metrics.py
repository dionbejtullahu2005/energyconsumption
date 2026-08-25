from __future__ import annotations
from typing import Any

import numpy as np
import pandas as pd


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if pd.notna(denominator) and denominator != 0 else np.nan

def calculate_period_metrics(frame: pd.DataFrame) -> dict[str, float]:
    consumption = frame.loc[frame["energy_flow"].eq("consumption_import")].copy()
    hourly = consumption.groupby(
        ["interval_start", "is_peak_07_18", "is_weekend"], as_index=False, observed=True
    )["energy_kwh"].sum(min_count=1)
    values = hourly["energy_kwh"].dropna()
    peak_mean = hourly.loc[hourly["is_peak_07_18"], "energy_kwh"].mean()
    offpeak_mean = hourly.loc[~hourly["is_peak_07_18"], "energy_kwh"].mean()
    weekday_mean = hourly.loc[~hourly["is_weekend"], "energy_kwh"].mean()
    weekend_mean = hourly.loc[hourly["is_weekend"], "energy_kwh"].mean()
    mean = values.mean()
    return {
        "energy_total_kwh": float(consumption["energy_kwh"].sum(min_count=1)),
        "peak_offpeak_ratio": _safe_ratio(peak_mean, offpeak_mean),
        "weekday_weekend_ratio": _safe_ratio(weekday_mean, weekend_mean),
        "coefficient_of_variation": _safe_ratio(values.std(), mean),
        "load_factor": _safe_ratio(mean, values.max()),
        "coverage_share": float(consumption["energy_kwh"].notna().mean()) if len(consumption) else np.nan,
    }


def calculate_weather_metrics(daily: pd.DataFrame) -> dict[str, Any]:
    valid = daily.loc[daily["valid_hours"].gt(0) & daily["energy_total_kwh"].notna()]
    correlations = {
        "temperature_correlation": valid["energy_total_kwh"].corr(valid["temperature_2m_mean"]),
        "hdd_correlation": valid["energy_total_kwh"].corr(valid["hdd_18"]),
        "cdd_correlation": valid["energy_total_kwh"].corr(valid["cdd_18"]),
    }
    heating = pd.notna(correlations["hdd_correlation"]) and correlations["hdd_correlation"] >= 0.30
    cooling = pd.notna(correlations["cdd_correlation"]) and correlations["cdd_correlation"] >= 0.30
    label = (
        "Lidhje statistikore me ditët e ftohta dhe të nxehta" if heating and cooling else
        "Lidhje statistikore me ditët e ftohta" if heating else
        "Lidhje statistikore me ditët e nxehta" if cooling else "Pa lidhje të qartë me motin"
    )
    return {**correlations, "weather_sensitivity_label": label,
            "weather_analysis_reliable": len(valid) >= 90, "days_with_consumption": len(valid)}


def calculate_holiday_effect(daily: pd.DataFrame) -> float:
    frame = daily.copy()
    frame["weekday_number"] = frame["date"].dt.weekday
    normal = frame.loc[~frame["is_holiday_or_day_off"] & frame["valid_hours"].gt(0)]
    effects: list[float] = []
    for row in frame.loc[frame["is_holiday_or_day_off"] & frame["valid_hours"].gt(0)].itertuples():
        baseline = normal.loc[normal["weekday_number"].eq(row.weekday_number), "energy_total_kwh"].mean()
        if pd.notna(baseline) and baseline > 0:
            effects.append((row.energy_total_kwh - baseline) / baseline)
    return float(np.mean(effects)) if effects else np.nan
