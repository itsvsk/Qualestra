"""Create and validate portable Qualestra project configurations."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from sentinelqa import __version__
from sentinelqa.adapters import (
    load_invariants,
    load_project_adapter,
    resolve_config_reference,
)
from sentinelqa.config import load_yaml
from sentinelqa.delivery.configuration import load_delivery_provider_configuration

_APPLICATION = """\
id: example-local
name: Example Tasks
adapter: static
base_url: http://example-web:8080
api_base_url: http://example-api:8080
allowed_origins:
  - http://example-web:8080
  - http://example-api:8080
health_url: http://example-api:8080/health
persona_file: ../personas/example.yaml
invariant_file: ../invariants/example.yaml
risk_areas:
  authorization: critical
critical_journeys: [sign_in, list_tasks]
"""

_PERSONAS = """\
personas:
  viewer:
    name: Vera Viewer
    role: viewer
    username: vera@example.test
    authentication:
      type: bearer
      token_ref: env:EXAMPLE_VIEWER_TOKEN
  owner:
    name: Owen Owner
    role: owner
    username: owen@example.test
    authentication:
      type: form
      login_path: /session
      username_ref: env:EXAMPLE_OWNER_USERNAME
      password_ref: env:EXAMPLE_OWNER_PASSWORD
"""

_INVARIANTS = """\
version: 1
invariants:
  - id: health_is_ready
    description: The application reports that it is ready.
    severity: critical
    request: {method: GET, path: /health}
    expect:
      status: 200
      max_duration_ms: 2000
      json:
        - {path: $.status, operator: equals, value: ready}
  - id: viewer_can_list_tasks
    description: A viewer can read the task collection.
    severity: high
    persona: viewer
    request: {method: GET, path: /v1/tasks}
    expect: {status: 200}
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qualestra")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a portable project configuration")
    init.add_argument("directory", nargs="?", default="qualestra-project")
    init.add_argument("--force", action="store_true", help="replace generated template files")
    validate = commands.add_parser("validate", help="validate an application and its references")
    validate.add_argument("application", help="path to an application YAML file")
    doctor = commands.add_parser(
        "doctor", help="check a standalone project configuration without contacting its targets"
    )
    doctor.add_argument("directory", nargs="?", default="qualestra-project")
    doctor.add_argument(
        "--delivery-config",
        help="optional provider configuration to validate without resolving credentials",
    )
    doctor.add_argument(
        "--require-secrets",
        action="store_true",
        help="fail when a referenced credential environment variable is absent",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "init":
        return _init(Path(arguments.directory), force=bool(arguments.force))
    if arguments.command == "validate":
        return _validate(Path(arguments.application))
    if arguments.command == "doctor":
        return _doctor(
            Path(arguments.directory),
            delivery_config=(
                Path(arguments.delivery_config) if arguments.delivery_config is not None else None
            ),
            require_secrets=bool(arguments.require_secrets),
        )
    raise AssertionError("unreachable command")


def _init(directory: Path, *, force: bool) -> int:
    targets = {
        directory / "applications" / "example.yaml": _APPLICATION,
        directory / "personas" / "example.yaml": _PERSONAS,
        directory / "invariants" / "example.yaml": _INVARIANTS,
    }
    existing = [str(path) for path in targets if path.exists()]
    if existing and not force:
        print(json.dumps({"ok": False, "error": "files_exist", "files": existing}))
        return 2
    for path, content in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "application": str(directory / "applications" / "example.yaml"),
                "files_created": len(targets),
            }
        )
    )
    return 0


def _validate(application_path: Path) -> int:
    try:
        payload, _ = _inspect_application(application_path)
        payload["ok"] = True
        print(json.dumps(payload, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, ValidationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1


def _doctor(
    directory: Path,
    *,
    delivery_config: Path | None,
    require_secrets: bool,
) -> int:
    issues: list[str] = []
    application_summaries: list[dict[str, Any]] = []
    secret_names: set[str] = set()
    application_directory = directory / "applications"
    application_paths = sorted(application_directory.glob("*.yaml"))
    if not application_paths:
        issues.append("no application YAML files found")
    for path in application_paths:
        try:
            summary, references = _inspect_application(path)
            application_summaries.append(summary)
            secret_names.update(references)
        except (OSError, ValueError, KeyError, ValidationError) as exc:
            issues.append(f"{path.name}: {exc}")

    identifiers = [str(item["application_id"]) for item in application_summaries]
    if len(identifiers) != len(set(identifiers)):
        issues.append("application ids must be unique")

    destination_count = 0
    if delivery_config is not None:
        try:
            providers = load_delivery_provider_configuration(delivery_config)
            destination_count = len(providers.destinations)
            for destination in providers.destinations:
                values = destination.model_dump(mode="json")
                secret_names.update(_configured_secret_names(values))
        except (OSError, ValueError, ValidationError) as exc:
            issues.append(f"delivery configuration: {exc}")

    missing_secret_names = sorted(name for name in secret_names if name not in os.environ)
    if require_secrets and missing_secret_names:
        issues.append("required credential environment variables are unavailable")

    payload = {
        "ok": not issues,
        "applications": application_summaries,
        "delivery_destinations": destination_count,
        "required_secret_env": sorted(secret_names),
        "missing_secret_env": missing_secret_names,
        "issues": issues,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if not issues else 1


def _inspect_application(application_path: Path) -> tuple[dict[str, Any], set[str]]:
    adapter = load_project_adapter(application_path)
    application = adapter.application
    invariant_count = _invariant_count(application_path, application)
    references = _configured_secret_names(
        {
            persona_id: persona.model_dump(mode="json")
            for persona_id, persona in adapter.personas().items()
        }
    )
    return (
        {
            "application_id": application.id,
            "adapter": application.adapter,
            "allowed_origins": len(application.allowed_origins),
            "personas": len(adapter.personas()),
            "invariants": invariant_count,
        },
        references,
    )


def _invariant_count(application_path: Path, application: Any) -> int:
    if application.invariant_file:
        invariant_path = resolve_config_reference(application_path, application.invariant_file)
        return len(load_invariants(invariant_path).invariants)
    if not application.legacy_invariant_file:
        return 0
    invariant_path = resolve_config_reference(application_path, application.legacy_invariant_file)
    items = load_yaml(invariant_path).get("invariants")
    if not isinstance(items, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or item.get("severity") not in {"low", "medium", "high", "critical"}
        for item in items
    ):
        raise ValueError("legacy invariants must define an id and valid severity")
    identifiers = [str(item["id"]) for item in items]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("legacy invariant ids must be unique")
    return len(items)


def _configured_secret_names(value: Any, field_name: str = "") -> set[str]:
    if isinstance(value, dict):
        names: set[str] = set()
        for key, item in value.items():
            names.update(_configured_secret_names(item, str(key)))
        return names
    if isinstance(value, (list, tuple)):
        names = set()
        for item in value:
            names.update(_configured_secret_names(item, field_name))
        return names
    if isinstance(value, str):
        if value.startswith("env:"):
            return {value.removeprefix("env:")}
        if field_name.endswith("_env"):
            return {value}
    return set()
