from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED_DIRECTORIES = frozenset(
    {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv", "dist", "node_modules"}
)
MAX_FILE_BYTES = 5_000_000
PATTERNS = {
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "OpenAI API key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def candidate_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and not any(part in EXCLUDED_DIRECTORIES for part in path.relative_to(ROOT).parts)
        and path.stat().st_size <= MAX_FILE_BYTES
    )


def main() -> int:
    findings: list[tuple[Path, int, str]] = []
    for path in candidate_files():
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(content.splitlines(), start=1):
            for finding_type, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((path.relative_to(ROOT), line_number, finding_type))
    for path, line_number, finding_type in findings:
        print(f"{path}:{line_number}: possible {finding_type} (value redacted)")
    if findings:
        print(f"secret scan failed with {len(findings)} possible finding(s)")
        return 1
    print(f"secret scan passed across {len(candidate_files())} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
