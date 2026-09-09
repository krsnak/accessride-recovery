#!/usr/bin/env python3
"""Run the Phase 6 offline smoke-test preflight; this cannot contact CALL-E."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from accessride.services.smoke_preflight import scenario_from_env, validate_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline-only AccessRide CALL-E smoke preflight")
    parser.add_argument("--operator-one-shot-approval", action="store_true",
                        help="validate that an explicit operator approval is present; never executes a call")
    args = parser.parse_args()
    report = validate_preflight(scenario_from_env(os.environ), repo_root=Path(__file__).parents[1],
                                one_shot_operator_flag=args.operator_one_shot_approval)
    print("PASS: offline preflight complete" if report.ok else "BLOCKED: offline preflight failed")
    for item in report.diagnostics:
        print(f"- {item}")
    print(f"live_execution_enabled={str(report.live_execution_enabled).lower()}")
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
