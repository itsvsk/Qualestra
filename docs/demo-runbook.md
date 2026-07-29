# Demo runbook

## Setup

```bash
cp .env.example .env
docker compose up --build --wait
```

Atlas is at `http://localhost:5173`, the control API at `http://localhost:8000`, and the dashboard
at `http://localhost:3000`. Atlas API and test-control endpoints are intentionally reachable only
inside the Compose network.

## Ten-minute path

1. Start with `ATLAS_FAULT_PROFILE=all docker compose up --build --wait`.
2. Create a run with `POST /v1/runs` for Atlas, owner/viewer, and seed 41021.
3. Show the task DAG and partition budgets. Point out that agents share no mutable browser page.
4. Show a restricted action with rationale `restricted_action_denied`.
5. Open an authorization or tenant anomaly and its raw request/response evidence.
6. Show two clean reset/replay attempts, minimized steps, and confidence.
7. Open JSON/Markdown defect output and the deterministic BLOCK operands.
8. Switch Atlas with `ATLAS_FAULT_PROFILE=fixed docker compose up --detach --wait --force-recreate atlas-api`, create a verification run, and show replay plus neighboring coverage.
9. Show PASS and the developer handoff `verified-defects.json`.

The benchmark-only proof is `make benchmark`; it requires no browser or model access.
