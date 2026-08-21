import importlib.util
from pathlib import Path

def test_app_source_compiles() -> None:
    source = Path(__file__).resolve().parents[1] / "app.py"
    compile(source.read_text(encoding="utf-8"), str(source), "exec")

