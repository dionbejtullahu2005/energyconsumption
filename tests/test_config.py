from pathlib import Path
from enerco_analysis.config import ProjectPaths, load_config

def test_default_configuration_is_valid() -> None:
    root = Path(__file__).resolve().parents[1]
    paths = ProjectPaths.from_root(root)
    config = load_config(paths.config_file)
    assert config["analysis"]["start_date"] == "2025-06-01"
    assert config["analysis"]["peak_hours"] == [7, 18]
    assert config["privacy"]["allow_real_company_names_in_outputs"] is False

