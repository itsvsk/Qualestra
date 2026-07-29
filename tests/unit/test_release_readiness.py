from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from scripts import release_check
from scripts.release_check import check_release

ROOT = Path(__file__).resolve().parents[2]


def test_release_candidate_passes_offline_readiness_checks() -> None:
    result = check_release(ROOT)

    assert result.ok is True
    assert result.version == "0.1.0"
    assert result.migration_heads == ("0005",)
    assert "standalone_compose" in result.checks
    assert "console_entry_point" in result.checks
    assert "license_metadata" in result.checks
    assert result.warnings == ()


def test_public_release_accepts_declared_apache_license() -> None:
    result = check_release(ROOT, public_release=True)

    assert result.ok is True
    assert result.issues == ()
    assert result.warnings == ()


def test_release_fails_closed_when_license_notice_drifts(monkeypatch: MonkeyPatch) -> None:
    original_read_text = release_check._read_text

    def read_text_without_notice(path: Path, issues: list[str]) -> str:
        if path.name == "NOTICE":
            return ""
        return original_read_text(path, issues)

    monkeypatch.setattr(release_check, "_read_text", read_text_without_notice)

    result = check_release(ROOT, public_release=True)

    assert result.ok is False
    assert result.issues == (
        "release requires Apache-2.0 LICENSE, NOTICE, package metadata, and OCI labels",
    )
