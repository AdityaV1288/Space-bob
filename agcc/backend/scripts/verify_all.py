"""One-command Task 19 cross-stack acceptance runner."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def run(*command: str, cwd: Path | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=cwd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()
    python = sys.executable
    verification_tmp = Path(__file__).resolve().parents[1] / ".verification-tmp"
    verification_tmp.mkdir(exist_ok=True)

    run(
        python,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(verification_tmp / "pytest"),
    )
    golden = [python, "scripts/run_golden_verification.py"]
    if args.benchmark:
        golden.append("--benchmark")
    run(*golden)
    run(python, "-m", "ruff", "check", ".")
    run(python, "-m", "mypy", "src/agcc")
    frontend = Path(__file__).resolve().parents[2] / "frontend"
    npm = "npm.cmd" if os.name == "nt" else "npm"
    run(npm, "test", "--", "--run", cwd=frontend)
    run(npm, "run", "build", cwd=frontend)
    print("Task 19 cross-stack verification PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
