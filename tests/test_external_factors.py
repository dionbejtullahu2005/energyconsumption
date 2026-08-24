import pandas as pd
import pytest

from enerco_analysis.external_factors import (
    _aggregate_company_daily,
    _company_district_membership,
    _holiday_effects,
    _prishtina_proxy_company_daily,
    _weather_sensitivity,
    meter_prefix,
)


def test_meter_prefix_extracts_district_code() -> None:
    assert meter_prefix("DPR123456") == "DPR"
    assert meter_prefix(" dGj-42 ") == "DGJ"
    assert meter_prefix("123") is None


def test_multi_district_company_uses_fixed_historical_weights() -> None:
    rows = []
    for date, energy_pr, energy_pz in [("2025-06-01", 75.0, 25.0), ("2025-06-02", 0.0, 1000.0)]:
        for meter, prefix, district, energy, temperature in [
            ("DPR1", "DPR", "Prishtinë", energy_pr, 10.0),
            ("DPZ1", "DPZ", "Prizren", energy_pz, 20.0),
        ]:
            rows.append({"company_id": "Kompania 1", "meter_id": meter, "meter_prefix": prefix,
                "district": district, "date": pd.Timestamp(date).date(), "energy_total_kwh": energy,
                "valid_hours": 24, "temperature_2m_mean": temperature,
                "temperature_2m_min": temperature - 2, "temperature_2m_max": temperature + 2,
                "hdd_18": max(18-temperature, 0), "cdd_18": max(temperature-18, 0),
                "is_holiday_or_day_off": False, "holiday_name": None})
    meter_daily = pd.DataFrame(rows)
    membership = _company_district_membership(meter_daily)
    company_daily = _aggregate_company_daily(meter_daily, membership)
    # Historical weights are 75/1100 and 1025/1100, and remain identical on both days.
    expected = 10 * (75/1100) + 20 * (1025/1100)
    assert company_daily["temperature_2m_mean"].round(8).nunique() == 1
    assert company_daily.iloc[0]["temperature_2m_mean"] == pytest.approx(expected)


def test_prishtina_proxy_replaces_weather_but_preserves_energy() -> None:
    company_daily = pd.DataFrame({
        "company_id": ["Kompania 1"], "date": [pd.Timestamp("2025-06-01").date()],
        "energy_total_kwh": [123.0], "valid_hours": [24], "reporting_meters": [1],
        "is_holiday_or_day_off": [False], "holiday_name": [None],
        "temperature_2m_mean": [25.0], "temperature_2m_min": [20.0],
        "temperature_2m_max": [30.0], "hdd_18": [0.0], "cdd_18": [7.0],
        "district_count": [1], "districts": ["Ferizaj"], "district_scope": ["Ferizaj"],
        "temperature_method": ["Temperaturë lokale e distriktit"],
    })
    weather = pd.DataFrame({
        "meter_prefix": ["DPR", "DFE"], "date": [pd.Timestamp("2025-06-01").date()] * 2,
        "temperature_2m_mean": [15.0, 25.0], "temperature_2m_min": [10.0, 20.0],
        "temperature_2m_max": [20.0, 30.0], "hdd_18": [3.0, 0.0], "cdd_18": [0.0, 7.0],
    })
    proxy = _prishtina_proxy_company_daily(company_daily, weather).iloc[0]
    assert proxy["energy_total_kwh"] == 123.0
    assert proxy["temperature_2m_mean"] == 15.0
    assert proxy["temperature_method"] == "Temperatura e Prishtinës si proxy"


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
