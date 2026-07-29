from __future__ import annotations

from pathlib import Path
from typing import Any

from sentinelqa.config import load_yaml


def load_gate_policy(path: str | Path, profile: str = "pull_request") -> dict[str, Any]:
    document = load_yaml(path)
    try:
        policy = document["profiles"][profile]
    except KeyError as exc:
        raise ValueError(f"unknown gate profile: {profile}") from exc
    if not isinstance(policy, dict):
        raise ValueError(f"gate profile is not a mapping: {profile}")
    return policy
