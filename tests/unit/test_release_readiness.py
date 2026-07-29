from __future__ import annotations

from pathlib import Path

from scripts.release_check import check_release

ROOT = Path(__file__).resolve().parents[2]


def test_release_candidate_passes_offline_readiness_checks() -> None:
    result = check_release(ROOT)

    assert result.ok is True
    assert result.version == "0.1.0"
    assert result.migration_heads == ("0005",)
    assert "standalone_compose" in result.checks
    assert "console_entry_point" in result.checks
    assert result.warnings == (
        "public release remains blocked until the owner selects and declares a license",
    )


def test_public_release_fails_closed_without_owner_selected_license() -> None:
    result = check_release(ROOT, public_release=True)

    assert result.ok is False
    assert result.issues == (
        "public release requires an owner-selected LICENSE plus pyproject license metadata",
    )
