# Qualestra project overview

## Project description

Qualestra is a supervised, evidence-first agent orchestration system for software quality
assurance. It coordinates six specialized logical agents across change analysis, UI exploration,
API exploration, business-rule checking, independent verification, and defect triage. A
deterministic supervisor controls their task graph, budgets, permissions, retries, and lifecycle.

The central design choice is separation of reasoning from authority. AI may form and prioritize
testing hypotheses, but a policy engine authorizes every browser, HTTP, storage, and delivery action.
The system records observations and evidence independently of model interpretation. Verification
must reproduce suspicious behaviour before it becomes a defect, and a pure quality-gate evaluator
calculates the final `PASS`, `WARN`, or `BLOCK` recommendation.

## Intended users

Qualestra is designed for:

- QA engineers who want broader exploratory coverage without losing reproducibility;
- development teams that need evidence-rich defects and release recommendations;
- platform teams evaluating controlled agentic workflows;
- engineering leaders seeking auditable automation around release confidence; and
- researchers and builders studying supervised multi-agent system design.

## Core outcomes

A Qualestra run is intended to produce:

- a durable record of what was planned, attempted, observed, and verified;
- redacted evidence for UI, API, accessibility, network, console, and invariant findings;
- minimized reproduction paths for verified defects;
- deterministic reports suitable for engineering and release stakeholders;
- approval-gated ticket, alert, or archive delivery; and
- an explainable release recommendation derived from versioned quality policy.

## What makes it different

Qualestra is not an unrestricted swarm. Agents cannot create peers, grant capabilities, choose
provider endpoints, access raw secrets, or decide the release gate. It is also not merely a test
generator: execution, observation, replay, reporting, delivery, and release policy are first-class
parts of the same system.

Its modular-monolith architecture keeps the initial deployment understandable and operable while
maintaining strong logical agent boundaries through typed contracts, scoped context, capability
views, budgets, and durable task leases.

## Current scope

The engineering preview includes:

- six statically registered logical agents;
- deterministic and OpenAI-backed model adapters;
- Playwright execution across Chromium, Firefox, and WebKit;
- safe API exploration and portable HTTP invariants;
- PostgreSQL-backed orchestration and evidence metadata;
- independent replay, confidence scoring, and defect triage;
- deterministic release gates and report rendering;
- a durable delivery outbox with filesystem, GitHub, Jira, and Slack connector boundaries;
- a React operator dashboard;
- a synthetic benchmark application with seeded defects; and
- offline CI, contract, integration, end-to-end, and release-readiness checks.

## Current limitations

Qualestra is not yet a hosted multi-tenant SaaS product. Provider administration, organization
isolation, enterprise identity, distributed browser capacity, bidirectional ticket synchronization,
long-running production hardening, and a formal public benchmark publication remain future work.
The default deployment is intended for local or private test environments.

Qualestra is an independent personal project by Vishal Appu Daniel and is not affiliated with,
sponsored by, or developed on behalf of any employer or other organization. It is licensed under
the Apache License 2.0; see [`LICENSE`](../LICENSE) and [`NOTICE`](../NOTICE).

## Public and private repository model

This public repository is a curated engineering snapshot. Product development and environment
testing occur in a separate private repository. Public updates should be exported as reviewed,
secret-scanned snapshots with a clean history so internal configurations, generated evidence, and
development-only records never enter the public repository.
