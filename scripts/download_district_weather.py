from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "project.json"
EXTERNAL_DIR = ROOT / "data" / "external"
API_URL = "https://archive-api.open-meteo.com/v1/archive"


def main() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    analysis = config["analysis"]
    settings = config["external_factors"]
    records: list[dict[str, object]] = []
    for meter_prefix, district in settings["districts"].items():
        params = {
            "latitude": district["latitude"],
            "longitude": district["longitude"],
            "start_date": analysis["start_date"],
            "end_date": analysis["end_date"],
            "hourly": "temperature_2m",
            "daily": "temperature_2m_mean,temperature_2m_min,temperature_2m_max",
            "timezone": settings["timezone"],
        }
        url = f"{API_URL}?{urllib.parse.urlencode(params)}"
        print(f"Duke shkarkuar motin për {district['name']} ({meter_prefix})...")
        request = urllib.request.Request(url, headers={"User-Agent": "EnerCo-Energy-Analysis/0.1"})
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)
        if payload.get("error"):
            raise RuntimeError(f"Open-Meteo: {payload.get('reason')}")
        records.append(
            {
                "meter_prefix": meter_prefix,
                "district": district["name"],
                "requested_latitude": district["latitude"],
                "requested_longitude": district["longitude"],
                "request_url": url,
                "response": payload,
            }
        )

    output = {
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Open-Meteo Historical Weather API",
        "source_url": API_URL,
        "analysis_start_date": analysis["start_date"],
        "analysis_end_date": analysis["end_date"],
        "timezone": settings["timezone"],
        "districts": records,
    }
    EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    output_path = EXTERNAL_DIR / settings["weather_raw_filename"]
    temporary = output_path.with_suffix(".tmp.json")
    temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output_path)
    print(f"U krijua: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
