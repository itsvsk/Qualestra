from __future__ import annotations

from typing import Any


async def resolve_control(page: Any, binding: dict[str, str]) -> tuple[Any, str]:
    """Resolve a trusted observer-produced binding; model output never reaches this function."""
    if test_id := binding.get("test_id"):
        return page.get_by_test_id(test_id), "test_id"
    if role := binding.get("role"):
        return page.get_by_role(role, name=binding.get("name"), exact=True), "role_name"
    if label := binding.get("label"):
        return page.get_by_label(label, exact=True), "label"
    if stable_attribute := binding.get("stable_attribute"):
        return page.locator(stable_attribute), "stable_attribute"
    raise ValueError("control has no trusted locator binding")
