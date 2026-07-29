# Contributing to Qualestra

Qualestra accepts focused changes that preserve deterministic supervision, typed contracts,
policy-controlled side effects, and offline test behavior.

## Development setup

Use Python 3.12+, `uv`, Node.js 22+, pnpm, and Docker with Compose:

```bash
make install
pnpm install --frozen-lockfile
make check
pnpm --filter @sentinelqa/dashboard lint
pnpm --filter @sentinelqa/dashboard test
pnpm --filter @sentinelqa/dashboard build
```

Run `uv run python scripts/release_check.py` when changing packaging, deployment, migrations,
versions, release documentation, or workflows.

## Change expectations

- Start from an issue or a narrowly described change.
- Update JSON contracts and contract tests together.
- Add an Alembic migration for persistent schema changes.
- Keep external endpoints and credential references in trusted server configuration.
- Add deterministic tests using the mock model and offline transports.
- Describe acceptance evidence, decisions, and remaining gaps in the pull request.
- Do not weaken a test, policy, approval, redaction, or release gate to make a change pass.

Security vulnerabilities must follow `SECURITY.md` rather than a public issue.
