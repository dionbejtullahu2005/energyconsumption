import pandas as pd
import pytest

from enerco_analysis.ui_metrics import calculate_period_metrics


def test_period_metrics_use_only_supplied_rows() -> None:
    frame = pd.DataFrame({
        "energy_flow": ["consumption_import"] * 4,
        "interval_start": pd.to_datetime([
            "2025-06-02 08:00", "2025-06-02 20:00", "2025-06-07 08:00", "2025-06-07 20:00"
        ]),
        "is_peak_07_18": [True, False, True, False],
        "is_weekend": [False, False, True, True],
        "energy_kwh": [20.0, 10.0, 10.0, 10.0],
    })
    result = calculate_period_metrics(frame)
    assert result["energy_total_kwh"] == 50.0
    assert result["peak_offpeak_ratio"] == 1.5
    assert result["weekday_weekend_ratio"] == 1.5
    assert result["load_factor"] == pytest.approx(0.625)
    assert result["coverage_share"] == 1.0
