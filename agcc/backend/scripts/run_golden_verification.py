"""Generate or compare Task 14 golden artifacts and print benchmark metrics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from agcc.verification.runner import GoldenVerificationRunner


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true", help="Replace checked-in artifacts")
    parser.add_argument("--benchmark", action="store_true", help="Print timing and baselines")
    args = parser.parse_args()
    output_dir = Path(__file__).resolve().parents[2] / "data" / "golden"
    output_dir.mkdir(parents=True, exist_ok=True)

    artifacts, report = GoldenVerificationRunner().run()
    mismatches = []
    for stage, value in artifacts.items():
        path = output_dir / f"{stage}.json"
        if args.update:
            _write_json(path, value)
        elif not path.exists():
            mismatches.append(f"missing {path.name}")
        else:
            expected = json.loads(path.read_text(encoding="utf-8"))
            if expected != value:
                mismatches.append(f"changed {path.name}")

    manifest = {
        "schema_version": "golden.v1",
        "artifact_hash": report.artifact_hash,
        "stage_count": len(artifacts),
    }
    manifest_path = output_dir / "manifest.json"
    if args.update:
        _write_json(manifest_path, manifest)
    elif (
        not manifest_path.exists()
        or json.loads(manifest_path.read_text(encoding="utf-8")) != manifest
    ):
        mismatches.append("changed manifest.json")

    if args.benchmark:
        print(report.model_dump_json(indent=2))
    if report.status != "pass":
        print("Golden correctness assertions failed", file=sys.stderr)
        return 1
    if mismatches:
        print("Golden artifact mismatch: " + ", ".join(mismatches), file=sys.stderr)
        return 1
    print(f"Golden verification PASS ({report.artifact_hash})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
