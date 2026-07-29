from __future__ import annotations

import json
from pathlib import Path

import pytest

from sentinelqa.cli.main import main


def test_init_and_validate_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = tmp_path / "portable"
    assert main(["init", str(project)]) == 0
    init_output = json.loads(_read_stdout(capsys))
    assert init_output["files_created"] == 3

    application = project / "applications" / "example.yaml"
    assert main(["validate", str(application)]) == 0
    validation = json.loads(_read_stdout(capsys))
    assert validation == {
        "adapter": "static",
        "allowed_origins": 2,
        "application_id": "example-local",
        "invariants": 2,
        "ok": True,
        "personas": 2,
    }

    assert main(["init", str(project)]) == 2
    assert json.loads(_read_stdout(capsys))["error"] == "files_exist"


def test_validate_reports_invalid_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    invalid = tmp_path / "bad.yaml"
    invalid.write_text("id: incomplete\n", encoding="utf-8")
    assert main(["validate", str(invalid)]) == 1
    output = json.loads(_read_stdout(capsys))
    assert output["ok"] is False
    assert "base_url" in output["error"]


def test_validate_reports_legacy_atlas_invariants(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", "config/applications/atlas.yaml"]) == 0
    output = json.loads(_read_stdout(capsys))
    assert output["application_id"] == "atlas-local"
    assert output["invariants"] == 9


def test_doctor_checks_generated_project_without_resolving_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "portable"
    assert main(["init", str(project)]) == 0
    _read_stdout(capsys)

    assert main(["doctor", str(project)]) == 0
    output = json.loads(_read_stdout(capsys))

    assert output["ok"] is True
    assert output["applications"][0]["application_id"] == "example-local"
    assert output["required_secret_env"] == [
        "EXAMPLE_OWNER_PASSWORD",
        "EXAMPLE_OWNER_USERNAME",
        "EXAMPLE_VIEWER_TOKEN",
    ]
    assert output["missing_secret_env"] == output["required_secret_env"]
    assert "private" not in json.dumps(output).lower()


def test_doctor_can_require_referenced_secret_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "portable"
    assert main(["init", str(project)]) == 0
    _read_stdout(capsys)

    assert main(["doctor", str(project), "--require-secrets"]) == 1
    output = json.loads(_read_stdout(capsys))

    assert output["ok"] is False
    assert output["issues"] == ["required credential environment variables are unavailable"]


def test_doctor_validates_provider_configuration_without_exposing_destinations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "portable"
    assert main(["init", str(project)]) == 0
    _read_stdout(capsys)

    assert (
        main(
            [
                "doctor",
                str(project),
                "--delivery-config",
                "config/delivery-providers.example.yaml",
            ]
        )
        == 0
    )
    output = json.loads(_read_stdout(capsys))

    assert output["delivery_destinations"] == 3
    assert {
        "SENTINEL_GITHUB_ISSUES_TOKEN",
        "SENTINEL_JIRA_API_TOKEN",
        "SENTINEL_SLACK_BOT_TOKEN",
    }.issubset(output["required_secret_env"])
    serialized = json.dumps(output)
    assert "https://" not in serialized
    assert "C0123456789" not in serialized


def test_cli_exposes_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--version"])

    assert raised.value.code == 0
    assert _read_stdout(capsys).strip() == "qualestra 0.1.0"


def _read_stdout(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().out
