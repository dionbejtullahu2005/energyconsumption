import pandas as pd
from enerco_analysis.external_factors import _holiday_effects, _weather_sensitivity

def test_weather_sensitivity_detects_cooling_pattern() -> None:
    days = pd.date_range("2025-06-01", periods=100, freq="D")
    temperature = pd.Series(range(100), dtype=float) / 5 + 5
    daily = pd.DataFrame(
        {
            "company_id": ["Kompania 1"] * 100,
            "date": days.date,
            "energy_total_kwh": 100 + temperature * 10,
            "valid_hours": [24] * 100,
            "temperature_2m_mean": temperature,
            "hdd_18": (18 - temperature).clip(lower=0),
            "cdd_18": (temperature - 18).clip(lower=0),
        }
    )
    config = {"external_factors": {"weather_correlation_threshold": 0.30, "minimum_days_for_weather_analysis": 90}}
    result = _weather_sensitivity(daily, ["company_id"], config).iloc[0]
    assert result["cdd_correlation"] > 0.30
    assert result["weather_analysis_reliable"]


def test_holiday_effect_uses_same_weekday_baseline() -> None:
    dates = pd.date_range("2025-06-02", periods=15, freq="7D")
    frame = pd.DataFrame(
        {
            "company_id": ["Kompania 1"] * 15,
            "date": dates.date,
            "energy_total_kwh": [100.0] * 14 + [50.0],
            "valid_hours": [24] * 15,
            "is_holiday_or_day_off": [False] * 14 + [True],
            "holiday_name": [None] * 14 + ["Festë testuese"],
        }
    )
    detail, _ = _holiday_effects(frame)
    assert detail.iloc[0]["same_weekday_baseline_kwh"] == 100.0
    assert detail.iloc[0]["holiday_effect_pct"] == -0.5
