import pandas as pd

from enerco_analysis.profiles import _basic_metrics


def test_basic_profile_metrics() -> None:
    frame = pd.DataFrame(
        {
            "company_id": ["Kompania 1"] * 4,
            "energy_kwh": [1.0, 3.0, 1.0, 1.0],
            "is_peak_07_18": [True, True, False, False],
            "is_weekend": [False, False, True, True],
        }
    )
    result = _basic_metrics(frame, ["company_id"]).iloc[0]
    assert result["peak_offpeak_ratio"] == 2.0
    assert result["weekday_weekend_ratio"] == 2.0
    assert result["load_factor"] == 0.5
