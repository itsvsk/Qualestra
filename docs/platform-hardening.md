# Platform hardening and hosted-operation hooks

Qualestra remains offline-first: local artifacts, no-op telemetry, and one general worker pool are
the defaults. Hosted capabilities are opt-in and do not change logical-agent contracts, supervisor
decisions, policy enforcement, or quality-gate semantics.

## Artifact storage

`ArtifactStore` is the storage boundary used by evidence producers. Both implementations redact
eligible text before hashing, persist immutable SHA-256 and size metadata, verify every read, bound
individual object size, and support explicit retention maintenance:

- `LocalArtifactStore` uses atomic local-file replacement and survives process restarts;
- `S3ArtifactStore` uses content plus JSON metadata sidecars, supports S3-compatible endpoints,
  rolls back a content upload when its sidecar fails, and creates signed read URLs with a maximum
  one-hour lifetime.

The S3 SDK is optional. Install it with `uv sync --extra s3`, or inject another client implementing
the small `S3Client` protocol. Standard cloud credentials are read by that client and are never
copied into prompts, metadata, logs, or child-process configuration.

`build_artifact_store_from_env()` recognizes:

| Setting | Meaning |
|---|---|
| `SENTINEL_ARTIFACT_BACKEND` | `local` (default) or `s3` |
| `ARTIFACT_ROOT` | Local evidence directory |
| `SENTINEL_ARTIFACT_MAX_BYTES` | Maximum bytes per artifact |
| `SENTINEL_ARTIFACT_S3_BUCKET` | Required S3 bucket |
| `SENTINEL_ARTIFACT_S3_PREFIX` | Object-key prefix |
| `SENTINEL_ARTIFACT_S3_ENDPOINT` | Optional S3-compatible endpoint |
| `SENTINEL_ARTIFACT_S3_REGION` | Optional region |
| `SENTINEL_ARTIFACT_S3_SSE` | Server-side encryption mode; `AES256` by default |

Retention never runs implicitly on a request path. A trusted maintenance job supplies a
`RetentionPolicy(max_age=..., max_total_bytes=...)`; deleted IDs are returned for audit logging.
Storage deletion remains separate from blackboard/evidence-reference retention so an operator can
coordinate both sides without dangling references.

## Metrics, traces, and logs

The zero-dependency observability package supplies:

- `NoopMetrics` and `NoopTracer` for the default configuration;
- thread-safe `InMemoryMetrics` and `InMemoryTracer` for tests and embedded diagnostics;
- a typed metrics catalog matching the names in the implementation specification;
- `MetricsRecorder` helpers for run, agent-task, budget, queue, and gate signals;
- OpenTelemetry-shaped `start_as_current_span()` semantics and parent/trace correlation;
- `StructuredLogger`, which recursively redacts untrusted fields before emitting JSON.

Metric definitions enforce their exact low-cardinality label sets. Run IDs, task IDs, URLs, and
other correlation values belong in traces or structured logs, not metric labels. Trace attributes
whose names imply secrets, tokens, passwords, or credentials are rejected.

An optional collector can be started without changing the core stack:

```bash
docker compose --profile observability up --detach otel-collector
```

It accepts OTLP gRPC/HTTP and uses a bounded memory processor. Qualestra does not require this
collector; a hosted exporter can bridge the instrumentation protocols when telemetry leaves the
process.

## Worker pools and horizontal scaling

`WorkerPoolConfig` and `PartitionFilter` define capacity, accepted logical-agent types, target
partition prefixes, browser capability, and reset exclusivity. Filters serialize to a lease-query
payload, allowing PostgreSQL to filter before leasing rather than leasing and returning unsuitable
work. Empty filters preserve the single modular worker's current behavior.

Settings are:

- `SENTINEL_WORKER_POOL`;
- `SENTINEL_WORKER_CONCURRENCY` (1–128);
- `SENTINEL_WORKER_AGENT_TYPES` (comma-separated static agent types);
- `SENTINEL_WORKER_PARTITION_PREFIXES` (comma-separated safe prefixes);
- `SENTINEL_WORKER_BROWSER_CAPABLE`;
- `SENTINEL_WORKER_RESET_EXCLUSIVE` (requires concurrency 1).

These are operational pools behind the unchanged `AgentTask` contract—not peer agents or separate
agent semantics. The authenticated lease endpoint and PostgreSQL query apply the filter atomically
before leasing; the in-memory service follows the same selection behavior. General and specialized
pools can therefore scale without leasing and discarding unsuitable work.

## CI and supply-chain controls

Workflows now use read-only repository permissions and disable persisted checkout credentials.
Python and Compose evidence is retained for 14 days even after failures. Pull requests receive a
GitHub dependency review. A repository-owned, dependency-free scanner detects common cloud, API,
chat, source-control, and private-key credential formats while printing only type and location.
Dependabot checks Python, npm, GitHub Actions, and Docker dependencies weekly.

These controls do not make evidence public and do not upload runtime credentials. Hosted CI should
add environment-specific bucket encryption, object lifecycle rules, network egress restrictions,
and workload identity rather than long-lived cloud access keys.
