"""Fail-closed, offline checks for Qualestra distribution readiness."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory

from sentinelqa import __version__
from sentinelqa.delivery.configuration import load_delivery_provider_configuration

VERSION_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[.-][0-9A-Za-z.-]+)?$")


@dataclass(frozen=True, slots=True)
class ReleaseCheckResult:
    version: str
    migration_heads: tuple[str, ...]
    checks: tuple[str, ...]
    warnings: tuple[str, ...]
    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def payload(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "version": self.version,
            "migration_heads": list(self.migration_heads),
            "checks": list(self.checks),
            "warnings": list(self.warnings),
            "issues": list(self.issues),
        }


def check_release(root: Path, *, public_release: bool = False) -> ReleaseCheckResult:
    root = root.resolve()
    checks: list[str] = []
    warnings: list[str] = []
    issues: list[str] = []

    required_files = (
        "README.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "NOTICE",
        "SECURITY.md",
        "compose.standalone.yml",
        ".env.standalone.example",
        "docs/releasing.md",
        ".github/workflows/release.yml",
    )
    missing_files = [relative for relative in required_files if not (root / relative).is_file()]
    if missing_files:
        issues.append(f"required release files are missing: {', '.join(missing_files)}")
    else:
        checks.append("release_files")

    pyproject = _load_toml(root / "pyproject.toml", issues)
    project = cast(dict[str, Any], pyproject.get("project", {}))
    hatch = cast(dict[str, Any], pyproject.get("tool", {}).get("hatch", {}))
    dynamic = project.get("dynamic", [])
    version_config = cast(dict[str, Any], hatch.get("version", {}))
    if "version" not in dynamic or version_config.get("path") != "sentinelqa/__init__.py":
        issues.append("package version must be sourced from sentinelqa/__init__.py")
    elif VERSION_PATTERN.fullmatch(__version__) is None:
        issues.append("package version is not a normalized semantic version")
    else:
        checks.append("package_version")

    scripts = cast(dict[str, Any], project.get("scripts", {}))
    if scripts.get("qualestra") != "sentinelqa.cli.main:main":
        issues.append("the qualestra console entry point is missing")
    else:
        checks.append("console_entry_point")

    dashboard = _load_json(root / "apps/dashboard/package.json", issues)
    if dashboard.get("version") != __version__:
        issues.append("dashboard and Python package versions differ")
    else:
        checks.append("dashboard_version")

    migration_heads = _migration_heads(root, issues)
    if len(migration_heads) == 1:
        checks.append("single_migration_head")

    try:
        providers = load_delivery_provider_configuration(
            root / "config/delivery-providers.example.yaml"
        )
        if not providers.destinations:
            issues.append("provider example must include at least one destination")
        else:
            checks.append("provider_example")
    except (OSError, ValueError) as exc:
        issues.append(f"provider example is invalid: {exc}")

    compose = _load_yaml(root / "compose.standalone.yml", issues)
    services = cast(dict[str, Any], compose.get("services", {}))
    if not {"postgres", "qa-worker", "dashboard"}.issubset(services):
        issues.append("standalone Compose must define postgres, qa-worker, and dashboard")
    else:
        checks.append("standalone_compose")

    release_workflow = _read_text(root / ".github/workflows/release.yml", issues)
    if "--public-release" not in release_workflow or 'tags: ["v*"]' not in release_workflow:
        issues.append("release workflow must gate version tags through the public release check")
    else:
        checks.append("tag_release_workflow")

    ci_workflow = _read_text(root / ".github/workflows/ci.yml", issues)
    if "alembic current --check-heads" not in ci_workflow or ' = "0004"' in ci_workflow:
        issues.append("CI must validate the current Alembic head without a stale revision pin")
    else:
        checks.append("migration_ci")

    dockerfiles = (
        _read_text(root / "Dockerfile", issues),
        _read_text(root / "apps/dashboard/Dockerfile", issues),
    )
    if any("org.opencontainers.image.source" not in value for value in dockerfiles):
        issues.append("release images must carry OCI source metadata")
    else:
        checks.append("oci_metadata")

    license_text = _read_text(root / "LICENSE", issues)
    notice_text = _read_text(root / "NOTICE", issues)
    license_expression = project.get("license")
    declared_license_files = project.get("license-files")
    expected_license_files = {"LICENSE", "NOTICE"}
    license_ready = (
        "Apache License" in license_text
        and "Version 2.0, January 2004" in license_text
        and "Copyright 2026 Vishal Appu Daniel" in notice_text
        and license_expression == "Apache-2.0"
        and isinstance(declared_license_files, list)
        and expected_license_files.issubset(declared_license_files)
        and all('org.opencontainers.image.licenses="Apache-2.0"' in value for value in dockerfiles)
    )
    if not license_ready:
        issues.append(
            "release requires Apache-2.0 LICENSE, NOTICE, package metadata, and OCI labels"
        )
    else:
        checks.append("license_metadata")

    return ReleaseCheckResult(
        version=__version__,
        migration_heads=migration_heads,
        checks=tuple(checks),
        warnings=tuple(warnings),
        issues=tuple(issues),
    )


def _load_toml(path: Path, issues: list[str]) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            return tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        issues.append(f"{path.name} is invalid: {exc}")
        return {}


def _load_json(path: Path, issues: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(f"{path.name} is invalid: {exc}")
        return {}
    if not isinstance(value, dict):
        issues.append(f"{path.name} must contain a JSON object")
        return {}
    return cast(dict[str, Any], value)


def _load_yaml(path: Path, issues: list[str]) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        issues.append(f"{path.name} is invalid: {exc}")
        return {}
    if not isinstance(value, dict):
        issues.append(f"{path.name} must contain a YAML mapping")
        return {}
    return cast(dict[str, Any], value)


def _migration_heads(root: Path, issues: list[str]) -> tuple[str, ...]:
    try:
        config = Config()
        config.set_main_option("script_location", str(root / "atlas/migrations"))
        heads = tuple(sorted(ScriptDirectory.from_config(config).get_heads()))
    except Exception as exc:  # noqa: BLE001 - normalized to a bounded release issue
        issues.append(f"migration graph is invalid: {type(exc).__name__}")
        return ()
    if len(heads) != 1:
        issues.append(f"migration graph must have exactly one head; found {len(heads)}")
    return heads


def _read_text(path: Path, issues: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        issues.append(f"cannot read {path.name}: {exc}")
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--public-release", action="store_true")
    arguments = parser.parse_args()
    result = check_release(arguments.root, public_release=arguments.public_release)
    print(json.dumps(result.payload(), sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
