# Security policy

Qualestra is currently intended for private/local or single-organization test environments. It is
not approved as a shared hosted service. Never target production systems or applications that you
do not own or have explicit authorization to test.

## Reporting a vulnerability

Do not disclose suspected vulnerabilities in a public issue. Use GitHub's private vulnerability
reporting feature for `itsvsk/Qualestra`. If that feature is unavailable, contact the
maintainer at `vishal.appu.daniel@gmail.com` with a minimal description and wait for a secure
follow-up channel before sharing credentials, exploit data, customer information, or sensitive
artifacts.

Include the affected version or commit, deployment mode, impact, and safe reproduction steps. Never
include real secrets or personal data.

## Supported versions

No public stable version has been released yet. Security fixes currently target the latest
repository revision. A supported-version table will be added with the first stable release.

## Trust boundary

Model output cannot authorize tools, choose arbitrary endpoints, provide executable code, or decide
the release gate. External reports, tickets, and alerts use a durable approval-aware outbox and
trusted server-side destinations. Review `docs/threat-model.md` before deploying Qualestra.
