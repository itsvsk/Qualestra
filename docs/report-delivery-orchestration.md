# Report and delivery orchestration

Milestone 8 adds a deterministic, connector-neutral boundary between committed Qualestra evidence
and future ticketing or alert systems. Logical agents do not create reports, authorize delivery, or
call connectors. The control service derives reports from durable run state and owns the delivery
state machine.

## Boundaries

- Reports are projections of committed runs, tasks, coverage, verified defects, and gate decisions.
- Report facts and gate conclusions never depend on LLM prose.
- API callers choose configured connector and destination identifiers, never a URL, filesystem
  path, credential, selector, or executable value.
- Connector configuration is static server configuration. Secrets remain environment references
  and do not enter reports, events, delivery records, or fixtures.
- The supervisor and logical-agent interfaces are unchanged.

## Report lifecycle

1. The control service reads one durable run snapshot.
2. The report builder normalizes and sorts source data.
3. A source fingerprint is computed from that normalized input.
4. JSON, Markdown, or HTML is rendered deterministically.
5. The report digest is computed from the exact rendered content.
6. The immutable report snapshot is stored and a bounded `report.generated` event is appended.
7. Regeneration with identical committed input returns identical content, fingerprint, and digest.

## Delivery lifecycle

```text
dry_run   -> draft
manual    -> pending_approval -> queued
automatic -> queued

queued -> processing -> succeeded
                     -> failed -> queued (explicit retry)
```

The idempotency key covers the run, report digest, connector identifier, destination identifier,
and delivery mode. Creating the same request again returns the existing record. Approval is valid
only from `pending_approval`; retry is valid only from `failed`. Attempt history is append-only.

Milestone 8 provides only offline no-op and filesystem adapters. Provider-specific systems such as
GitHub Issues, Jira, Slack, Teams, email, or Linear are later adapters over the same protocol.

## Failure handling

- Connector exceptions are converted to bounded, redacted error metadata.
- A failed attempt never deletes the report snapshot or prior attempts.
- Processing claims are explicit so a restart can recover queued work without duplicating a
  completed delivery.
- Delivery events contain identifiers, status, and safe error classification only.
- Retrying preserves the original idempotency key and increments the attempt number.

## Acceptance evidence

Milestone completion requires contract validation, migration upgrade checks, in-memory and
PostgreSQL persistence tests, deterministic report tests, delivery state-machine tests, control API
tests, dashboard tests, and the existing repository quality gates.
