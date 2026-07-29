# Provider ticketing and alerts

Milestone 9 connects the durable Qualestra delivery outbox to GitHub Issues, Jira, and Slack.
These providers remain control-service adapters: logical agents cannot call them, choose their
URLs, supply credentials, write payload templates, approve manual delivery, or decide routing.

## Configure destinations

Provider integrations are disabled by default. Copy
`config/delivery-providers.example.yaml` to the ignored
`config/delivery-providers.yaml`, keep only the destinations in use, and set:

```text
SENTINEL_DELIVERY_CONFIG=/app/config/delivery-providers.yaml
```

Use `config/delivery-providers.yaml` instead when running the worker directly from the repository
rather than through Compose.

The tracked example contains only provider origins, repository/project/channel identifiers, labels,
display metadata, and environment-variable references. It never contains authentication values.
Inject each referenced environment variable into the `qa-worker` using the deployment's normal
secret mechanism. The process resolves a credential only when its configured destination is
actually delivered.

The local Compose stack mounts `config/` read-only, so destination changes require a worker restart
but not an image rebuild. Invalid YAML, unsafe HTTP origins, raw credential fields, duplicate
destination identifiers, or malformed provider identifiers fail closed during service startup.

## Supported providers

| Provider | Connector | Behavior |
|---|---|---|
| GitHub Issues | `github_issues` | Searches for the stable report marker, then creates one bounded issue if none exists |
| Jira | `jira` | Searches by a stable delivery label, then creates one bounded ADF issue if none exists |
| Slack | `slack` | Posts bounded release-gate blocks with a deterministic `client_msg_id` |

GitHub and Jira ticket titles, bodies, and labels are derived from the immutable stored report.
Slack content is similarly deterministic, redacted, mention-neutralized, and link-unfurling is
disabled. Provider response URLs are ignored; any returned ticket link is rebuilt from static
configuration and a validated provider identifier.

## Delivery and retry behavior

The dashboard lists only configured destination IDs, display names, provider names, kinds, and
supported delivery modes. It never receives origins, repository/project/channel configuration, or
credential references.

- `dry_run` records a draft without contacting a provider.
- `manual` requires explicit operator approval before the outbox can call a provider.
- `automatic` queues immediately, but remains a deterministic control-service action rather than an
  agent decision.
- GitHub and Jira retries first search for the stable marker, preventing a known accepted ticket
  from being recreated after an ambiguous transport failure.
- Slack sends the same `client_msg_id` on retry so Slack can recognize the repeated message.
- Authentication failures, throttling, timeouts, malformed or oversized responses, and provider
  rejections become bounded, secret-safe attempt records.

Provider HTTP clients reject redirects and cap response bodies. External response bodies, headers,
tokens, and raw exception details are never stored in reports, delivery history, or events.

## Current limits

This milestone creates outbound tickets and alerts only. It does not ingest comments, synchronize
ticket changes, automatically close tickets, administer OAuth installations, or add Teams, email,
Linear, PagerDuty, or arbitrary webhook providers.
