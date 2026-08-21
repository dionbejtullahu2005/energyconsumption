from enerco_analysis.quality import SeriesStats, _parse_meter_header

def test_parse_prosumer_headers() -> None:
    assert _parse_meter_header("DFE123 - A+", True) == ("DFE123", "consumption_import")
    assert _parse_meter_header("DFE123 - A-", True) == ("DFE123", "injection_export")

def test_long_zero_run_is_flagged() -> None:
    stat = SeriesStats("Prosumer", "Kompania 1", "M1", "injection_export")
    anomalies = []
    for hour in range(48):
        stat.add_value(0, ("2025-06-01", hour + 1), hour, 48, anomalies)
    stat.finish(48)
    assert stat.zero_run_events == 1
    assert stat.zero_run_hours == 48
    assert stat.maximum_zero_run == 48
