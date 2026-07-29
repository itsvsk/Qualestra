# Connecting another project to Qualestra

Qualestra projects are described by three version-controlled YAML documents: an application,
personas, and executable invariants. The definitions contain no credentials and cannot import
Python code. An explicit runtime registry is the only way to install a custom adapter.

## Generate and validate a project

```bash
qualestra init qualestra-project
qualestra validate qualestra-project/applications/example.yaml
qualestra doctor qualestra-project
```

`init` creates a safe starting point. `validate` checks URLs and their origin allowlist, referenced
files, personas, authentication modes, and every invariant before a run starts. It does not resolve
or print secrets. `doctor` validates every application in the generated directory, lists only the
names of referenced credential environment variables, and optionally checks provider configuration.
Use `--require-secrets` only at deployment time to fail when a required environment variable is
missing. The non-Atlas `config/applications/acme-tasks.yaml` example demonstrates the same layout in
this repository.

The standalone container sets `SENTINEL_APPLICATION_DIRECTORY=/project/applications` and mounts the
generated project directory read-only. This keeps project-owned applications, personas, and
invariants separate from Qualestra's trusted agent, policy, gate, and impact configuration.

## Authentication

Personas can select form login, bearer token, API-token header, or pre-issued OIDC-style token
injection. Credential values are always `env:NAME` references. Qualestra resolves a reference only
at the transport boundary; representations and HTTP evidence redact the resulting value. The OIDC
adapter deliberately injects a test token and never connects to a real identity provider.

## Invariants

Version 1 invariants support target-relative HTTP GET, POST, PUT, and PATCH requests, expected
statuses, maximum latency, and small typed JSON assertions (`equals`, `not_equals`, `contains`,
`exists`, and `length_equals`). Absolute URLs, path traversal, dynamic path interpolation,
credential-like JSON paths, and DELETE are rejected during parsing. Mutating rules require explicit
runtime authorization.

At execution, the supervisor resolves the run's declared target ID, projects its validated UI/API
URLs into agent context, and the worker prepares the selected persona only at the transport
boundary. `HttpInvariantRunner` executes each portable rule through a policy-authorized request;
every request is constrained to the configured origin and captured as redacted evidence. Browser
sessions apply form, header, or token authentication before navigation. API and invariant replay
carry the target ID so independent verification uses the same adapter.

Atlas retains a small `legacy_invariant_file` during migration because its relational probes compare
API, persisted state, audit cardinality, and export bytes. New integrations should use the version 1
portable invariant document referenced by `invariant_file`.

Custom behavior is provided by implementing `ProjectAdapter` and registering its factory on
`ProjectAdapterRegistry` in trusted startup code. A YAML file cannot name a module or cause a dynamic
import.
