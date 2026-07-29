# Qualestra enhancement roadmap

Updated 28 July 2026.

Implementation status: the project adapter, operator workflow, deeper testing, and platform
hardening sections are complete. The hosted-security section remains an explicit deployment gate
for any future shared SaaS offering; Qualestra's current supported deployment is a private/local or
single-organization test environment.

This roadmap orders enhancements by adoption value and dependency. A later phase may not weaken
the deterministic supervisor, policy boundary, independent verification, or evidence-led gate.

## Priority 0 — portable adoption and operator workflow

### Project adapter SDK and onboarding

Deliver typed application/authentication/reset/invariant contracts, a static adapter registry,
scaffold and validation commands, and a non-Atlas example.

Acceptance:

- a new project can be scaffolded and validated without editing the Qualestra engine;
- application origins, personas, credentials, reset support, and invariants fail closed;
- form, bearer-token, and OIDC-compatible test-session adapters redact secrets;
- a generic invariant executes through policy-scoped HTTP tools;
- Atlas remains a normal adapter rather than an engine special case.

### Operator control room

Deliver run creation, cancellation, rerun/verification, live refresh, state/transition coverage,
defect evidence, artifact downloads, gate operands, and run comparison.

Acceptance:

- an operator can complete the faulty-to-fixed workflow without using curl;
- active state refreshes automatically and terminal state stops unnecessary polling;
- artifact and trace links resolve through the control API;
- failures are visible without discarding already loaded evidence.

## Priority 1 — deeper and more relevant testing

### Change impact and exploration memory

Deliver configurable repository-to-feature mappings, OpenAPI operation impact, historical risk and
coverage signals, run comparison, and explicit flake classification.

Acceptance:

- identical inputs yield identical impact and ranking output;
- changed paths select direct, adjacent, and smoke scopes with recorded reasons;
- historical signals can raise priority but cannot authorize an action or decide a gate;
- intermittent environment failures are not reported as verified product defects.

### Browser, accessibility, and API depth

Deliver Chromium/Firefox/WebKit selection, viewport/locale/timezone options, deterministic
accessibility scanning, and generated safe API cases from OpenAPI.

Acceptance:

- unsupported browsers/configuration fail before a run starts;
- browser evidence identifies its browser and environment;
- critical/serious accessibility signals link to DOM/screenshot evidence;
- API generation respects method policy, request budgets, and allowlisted origins.

## Priority 2 — production hardening

### Storage, observability, CI, and scaling

Deliver pluggable local/S3-compatible artifact storage, integrity and retention metadata,
OpenTelemetry-compatible instrumentation, evidence-preserving CI integrations, and configurable
worker pools over the existing PostgreSQL lease contract.

Acceptance:

- storage backends share one immutable artifact contract and verify content digests;
- instrumentation has a safe no-op mode and never records secrets;
- CI always preserves reports and evidence and exposes the deterministic gate result;
- multiple workers can filter partitions without changing agent semantics.

### Hosted security

Deliver authenticated operator access, project isolation, signed artifact access, rate limits,
egress restrictions, and secret-manager integration before any shared hosted deployment.

Acceptance:

- anonymous mutation is denied when hosted mode is enabled;
- project-scoped users cannot read another project's runs or artifacts;
- test-control and seeded-fault endpoints remain private;
- dependency, secret, and image scanning run in CI.

## Priority 3 — report and delivery orchestration

Deliver deterministic report snapshots, a durable delivery outbox, approval/retry/idempotency
semantics, offline delivery adapters, control API operations, and dashboard delivery history.
Provider-specific ticketing and alert connectors build on this foundation in a later milestone.

Acceptance:

- identical committed evidence produces identical report content;
- delivery side effects are connector-neutral, auditable, redacted, and never agent-authorized;
- manual deliveries require approval and repeated requests do not duplicate delivery;
- delivery attempts and failures survive restart and can be retried deterministically;
- offline tests cover the complete report-to-delivery lifecycle without external credentials.

## Priority 4 — provider ticketing and alerts

Deliver server-configured GitHub Issues and Jira ticket creation, Slack release alerts,
non-secret destination discovery, provider-aware dashboard controls, and offline provider contract
tests over the durable Milestone 8 delivery outbox.

Acceptance:

- delivery requests still contain only configured connector and destination identifiers;
- provider URLs, repository/project/channel identifiers, and credential environment references are
  loaded only from trusted server configuration and never returned by the API;
- ticket and alert content is deterministically derived from immutable report snapshots;
- provider requests carry a stable idempotency marker and retries do not knowingly duplicate an
  already accepted ticket or Slack message;
- provider failures, throttling, and malformed responses become bounded redacted delivery failures;
- manual provider delivery cannot execute before approval, and provider attempt history survives
  restart through the existing outbox;
- all provider behavior is tested offline with injected fake HTTP transports and no real
  credentials or network access.

## Priority 5 — standalone release readiness

Deliver an installed onboarding CLI, offline configuration doctor, standalone Compose deployment,
versioned Python/container artifacts, OCI provenance metadata, fail-closed release checks, tag-only
release automation, and public contributor/security/release documentation.

Acceptance:

- the wheel exposes version, scaffold, validation, and doctor commands without a repository checkout;
- standalone deployment mounts project configuration independently and does not require Atlas;
- version, migration, workflow, provider-example, packaging, and deployment drift fail preflight;
- tag automation validates the complete suite before publishing Python archives or GHCR images;
- public release remains blocked until the owner selects and declares an explicit license;
- secrets and generated evidence are excluded from distribution artifacts.

## Explicit non-goals

- one network service per logical agent;
- allowing a model to execute arbitrary selectors, URLs, code, SQL, or shell commands;
- treating automated accessibility checks as full compliance;
- running destructive exploration against production;
- claiming that autonomous exploration proves an absence of defects.
