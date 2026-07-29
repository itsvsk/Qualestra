# Threat model

## Assets and trust

Trusted inputs are repository configuration, policy, code, and test credentials from the secret
environment. OpenAPI documents are conditionally trusted only from allowlisted targets. DOM text,
API bodies, console output, evidence, and all model output are untrusted. Test-control tokens and
database administration remain restricted.

## Primary threats and controls

| Threat | Control |
|---|---|
| Prompt injection in page text | Structured observation, enumerated actions/current control IDs, independent policy |
| Confused deputy between agents | Runtime-bound identity, static registry, context projection, capability `ToolView` |
| Unbounded cost or target load | Atomic parent/task budgets, deadlines, rate limits, cancellation, kill switch |
| Cross-origin or destructive action | Deny-by-default origins and safety classes; explicit approval only in ephemeral interactive runs |
| Secret disclosure | Structured header rules, recursive redaction, synthetic personas, no secrets in prompts |
| Partial or fabricated result | Schema validation and atomic commit; evidence references authorized to the run |
| False positive blocks release | Two clean replays, minimization, confidence threshold, deterministic gate |
| Worker abandonment | 10-second heartbeat, 45-second lease expiry, idempotency key, reset before unknown writes |

No active scan is permitted outside Atlas or another explicitly authorized ephemeral test target.
The browser and worker should run with denied external egress in hosted environments.
