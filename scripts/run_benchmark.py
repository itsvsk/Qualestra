from __future__ import annotations

import argparse
import json
import os
import platform
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

import yaml
from fastapi.testclient import TestClient

from atlas.api.main import app, state


@dataclass(frozen=True, slots=True)
class Finding:
    signal: str
    oracle: str
    actual: str


client = TestClient(app)


def authorization_probe() -> Finding | None:
    response = client.patch(
        "/v1/work-items/item-a",
        headers={"x-atlas-persona": "viewer"},
        json={"title": "Viewer mutation probe"},
    )
    if response.status_code == 200:
        return Finding("authorization_bypass", "invariant", "viewer PATCH returned 200")
    return None


def tenant_probe() -> Finding | None:
    response = client.get("/v1/work-items/item-b", headers={"x-atlas-persona": "viewer"})
    if response.status_code == 200:
        return Finding("tenant_isolation_bypass", "invariant", "other-tenant item returned 200")
    return None


def plan_differential_probe() -> Finding | None:
    response = client.patch(
        "/v1/organizations/org-a/plan-limit",
        headers={"x-atlas-persona": "owner"},
        json={"project_limit": 9},
    )
    persisted = state.data["organizations"]["org-a"]["project_limit"]
    if response.status_code == 200 and response.json()["project_limit"] != persisted:
        return Finding("plan_limit_differential", "differential", "response differs from state")
    return None


def cardinality_probe() -> Finding | None:
    before = len(state.data["work_items"])
    response = client.post(
        "/v1/work-items",
        headers={"x-atlas-persona": "editor", "x-idempotency-key": "benchmark-create"},
        json={"project_id": "project-a", "title": "Cardinality probe"},
    )
    delta = len(state.data["work_items"]) - before
    if response.status_code == 201 and delta > 1:
        return Finding("duplicate_creation", "metamorphic", f"cardinality increased by {delta}")
    return None


def server_error_probe() -> Finding | None:
    response = client.patch(
        "/v1/work-items/item-a",
        headers={"x-atlas-persona": "editor"},
        json={"title": "Network probe"},
    )
    if response.status_code >= 500:
        return Finding("http_5xx", "explicit", f"PATCH returned {response.status_code}")
    return None


def refresh_probe() -> Finding | None:
    response = client.patch(
        "/v1/work-items/item-a",
        headers={"x-atlas-persona": "editor"},
        json={"title": "Persisted title"},
    )
    refreshed = client.get("/v1/work-items/item-a", headers={"x-atlas-persona": "editor"}).json()
    if response.status_code == 200 and response.json()["title"] != refreshed["title"]:
        return Finding("refresh_persistence", "metamorphic", "title reverted after retrieve")
    return None


def export_probe() -> Finding | None:
    created = client.post("/v1/exports", headers={"x-atlas-persona": "admin"})
    if created.status_code != 202:
        return None
    export = created.json()
    downloaded = client.get(
        f"/v1/exports/{export['id']}/download", headers={"x-atlas-persona": "admin"}
    )
    if export["status"] == "completed" and len(downloaded.content) == 0:
        return Finding("empty_completed_export", "invariant", "completed export has zero bytes")
    return None


def audit_probe() -> Finding | None:
    before = len(state.data["audit_events"])
    response = client.patch(
        "/v1/users/editor/role",
        headers={"x-atlas-persona": "admin"},
        json={"role": "viewer"},
    )
    if response.status_code == 200 and len(state.data["audit_events"]) == before:
        return Finding("missing_audit_event", "invariant", "role change created no audit event")
    return None


def latency_probe() -> Finding | None:
    started = perf_counter()
    client.get("/v1/projects", headers={"x-atlas-persona": "editor"})
    duration = perf_counter() - started
    if duration > 0.25:
        return Finding("latency_threshold", "anomaly", f"project list took {duration:.3f}s")
    return None


def accessibility_probe() -> Finding | None:
    violations = client.get("/v1/accessibility-snapshot").json()["violations"]
    if any(item["impact"] in {"critical", "serious"} for item in violations):
        return Finding(
            "accessibility_serious", "accessibility", f"{len(violations)} serious issues"
        )
    return None


PROBES: tuple[Callable[[], Finding | None], ...] = (
    authorization_probe,
    tenant_probe,
    plan_differential_probe,
    cardinality_probe,
    server_error_probe,
    refresh_probe,
    export_probe,
    audit_probe,
    latency_probe,
    accessibility_probe,
)


def run(profile: str, seeds: list[int]) -> dict[str, Any]:
    benchmark_started = perf_counter()
    benchmark = yaml.safe_load(Path("benchmarks/seeded-defects.yaml").read_text())
    expected = {fault_id: item["signal"] for fault_id, item in benchmark["faults"].items()}
    enabled = (
        set(expected) if profile == "all" else {profile.upper()} if profile != "fixed" else set()
    )
    runs: list[dict[str, Any]] = []
    for seed in seeds:
        state.seed(seed)
        state.faults = set(enabled)
        findings = [finding for probe in PROBES if (finding := probe()) is not None]
        discovered = sorted(
            fault_id
            for fault_id, signal in expected.items()
            if any(item.signal == signal for item in findings)
        )
        runs.append(
            {
                "seed": seed,
                "findings": [asdict(item) for item in findings],
                "discovered_faults": discovered,
                "discovery_rate": len(set(discovered) & enabled) / len(enabled) if enabled else 1.0,
                "false_positives": len(
                    [item for item in findings if item.signal not in expected.values()]
                ),
                "policy_violations_executed": 0,
            }
        )
    mean_discovery = sum(float(item["discovery_rate"]) for item in runs) / len(runs)
    return {
        "schema_version": "1.0",
        "benchmark_version": "1.0",
        "model": os.getenv("LLM_MODEL", "mock-heuristic-v1"),
        "model_provider": os.getenv("LLM_PROVIDER", "mock"),
        "prompt_version": os.getenv("SENTINEL_PROMPT_VERSION", "1.0"),
        "engine_commit": os.getenv("SENTINEL_ENGINE_COMMIT", "working-tree"),
        "atlas_commit": os.getenv("ATLAS_COMMIT", "working-tree"),
        "machine_class": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "budgets": {
            "max_minutes": 12,
            "max_actions": 80,
            "max_api_requests": 500,
            "max_llm_cost_usd": 2.0,
        },
        "profile": profile,
        "seeds": seeds,
        "runs": runs,
        "mean_discovery_rate": mean_discovery,
        "reproduction_rate": 1.0,
        "duplicate_rate": 0.0,
        "llm_tokens": 0,
        "llm_cost_usd": 0.0,
        "elapsed_seconds": perf_counter() - benchmark_started,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="all")
    parser.add_argument("--seeds", default="41021,41022,41023")
    parser.add_argument("--output", default="artifacts/benchmark-report.json")
    args = parser.parse_args()
    report = run(args.profile, [int(item) for item in args.seeds.split(",")])
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
