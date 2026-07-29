from __future__ import annotations

import argparse
import time
from typing import Any

import httpx

TERMINAL = {"passed", "warned", "blocked", "cancelled", "failed"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Exercise a live Qualestra worker and gate")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--expected", choices=("passed", "blocked"), default="passed")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--browsers", default="chromium")
    parser.add_argument("--personas", default="owner,viewer")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with httpx.Client(base_url=args.base_url, timeout=10) as client:
        ready = client.get("/health/ready")
        ready.raise_for_status()
        if ready.json().get("blackboard") != "postgres":
            raise RuntimeError("live smoke requires the PostgreSQL blackboard")
        response = client.post(
            "/v1/runs",
            json={
                "target_id": "atlas-local",
                "trigger": "pull_request",
                "commit_sha": f"live-smoke-{args.expected}",
                "changed_files": ["atlas/api/main.py"],
                "browsers": [item.strip() for item in args.browsers.split(",") if item.strip()],
                "personas": [item.strip() for item in args.personas.split(",") if item.strip()],
                "seed": 41021,
            },
        )
        response.raise_for_status()
        run_id = response.json()["id"]
        deadline = time.monotonic() + args.timeout
        summary: dict[str, Any] = {}
        while time.monotonic() < deadline:
            response = client.get(f"/v1/runs/{run_id}")
            response.raise_for_status()
            summary = response.json()
            if summary["run"]["status"] in TERMINAL:
                break
            time.sleep(0.25)
        status = summary.get("run", {}).get("status")
        if status != args.expected:
            raise RuntimeError(f"expected {args.expected}, got {status}: {summary}")
        gate = client.get(f"/v1/gates/{run_id}")
        gate.raise_for_status()
        print(
            {
                "run_id": run_id,
                "status": status,
                "tasks": summary["task_count"],
                "defects": summary["defect_count"],
                "gate": gate.json()["outcome"],
            }
        )


if __name__ == "__main__":
    main()
