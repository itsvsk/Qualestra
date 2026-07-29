# Benchmark method

Atlas exposes ten switchable seeded defects. The planner never receives fault IDs or expected
outcomes. The harness applies reusable authorization, tenant, differential, cardinality, network,
persistence, job, audit, latency, and accessibility probes, then matches normalized signals to the
independently maintained mapping in `benchmarks/seeded-defects.yaml`.

Run three fixed seeds:

```bash
python scripts/run_benchmark.py --profile all --seeds 41021,41022,41023
```

Each report records signals, oracle category, discoveries, discovery rate, false positives,
executed policy violations, engine and Atlas commits, model/provider, prompt version, machine
class, budgets, elapsed time, token/cost use, reproduction rate, and duplicate rate. CI supplies
immutable commit identifiers through `SENTINEL_ENGINE_COMMIT` and `ATLAS_COMMIT`; an uncommitted
local run honestly reports `working-tree`. Fixed-profile runs are the false-positive control.

The in-process benchmark is a deterministic acceptance harness. The Compose demo adds real browser
traces, screenshots, HAR-like request/response evidence, and clean replay artifacts.

Run the k6 smoke baseline through Toxiproxy with `make reliability`. The reliability profile also
defines deterministic downstream latency, timeout, peer-reset, and bandwidth-limiting endpoints on
internal ports 18081–18084; they are intentionally not published to the host.
