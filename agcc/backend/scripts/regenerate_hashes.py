"""Regenerate raw_payload_hash for all weather and space-weather fixture files.

Uses the canonical_payload_hash algorithm:
  - Pop 'raw_payload_hash' from a copy
  - json.dumps with sort_keys=True, separators=(',',':'), ensure_ascii=True
  - SHA-256 hex digest
"""

import hashlib
import json
from pathlib import Path

BASE = Path(__file__).parent.parent.parent.parent / "data" / "fixtures"


def canonical_payload_hash(payload: dict) -> str:
    copy = dict(payload)
    copy.pop("raw_payload_hash", None)
    serialized = json.dumps(copy, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def update_weather_file(path: Path) -> int:
    raw = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    for station_id, records in raw.get("stations", {}).items():
        for record in records:
            record["raw_payload_hash"] = canonical_payload_hash(record)
            count += 1
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return count


def update_space_weather_file(path: Path) -> int:
    raw = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    for record in raw.get("snapshots", []):
        record["raw_payload_hash"] = canonical_payload_hash(record)
        count += 1
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return count


weather_files = [
    BASE / "weather" / "weather_clear.json",
    BASE / "weather" / "weather_heavy_rain.json",
    BASE / "weather" / "weather_moderate_rain.json",
    BASE / "weather" / "weather_missing_data.json",
]
space_weather_files = [
    BASE / "space_weather" / "space_weather_fixture.json",
]

total = 0
for f in weather_files:
    n = update_weather_file(f)
    print(f"Updated {n} records in {f.name}")
    total += n

for f in space_weather_files:
    n = update_space_weather_file(f)
    print(f"Updated {n} records in {f.name}")
    total += n

print(f"\nTotal: {total} records updated")
