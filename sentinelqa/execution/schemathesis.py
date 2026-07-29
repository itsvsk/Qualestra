from __future__ import annotations

from typing import Any

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def load_openapi(document: dict[str, Any]) -> Any:
    """Validate and load an OpenAPI document using the supported Schemathesis v4 API."""
    import schemathesis

    if not document.get("openapi") or not isinstance(document.get("paths"), dict):
        raise ValueError("invalid OpenAPI document")
    return schemathesis.openapi.from_dict(document)


def safe_operations(
    document: dict[str, Any], *, features: list[str] | None = None
) -> list[tuple[str, str, dict[str, Any]]]:
    """Return deterministic, read-only operations, prioritizing impacted feature names."""
    feature_names = {item.lower().replace("_", "-") for item in features or []}
    operations: list[tuple[int, str, str, dict[str, Any]]] = []
    for path, path_item in sorted(document.get("paths", {}).items()):
        if not isinstance(path_item, dict):
            continue
        for method, operation in sorted(path_item.items()):
            normalized_method = method.upper()
            if normalized_method not in SAFE_METHODS or not isinstance(operation, dict):
                continue
            haystack = f"{path} {operation.get('operationId', '')}".lower().replace("_", "-")
            priority = 0 if any(feature in haystack for feature in feature_names) else 1
            operations.append((priority, normalized_method, path, operation))
    return [(method, path, operation) for _, method, path, operation in sorted(operations)]


def response_schema(document_operation: dict[str, Any], status: int) -> dict[str, Any] | None:
    responses = document_operation.get("responses", {})
    response = responses.get(str(status), responses.get("default", {}))
    if not isinstance(response, dict):
        return None
    content = response.get("content", {})
    if not isinstance(content, dict):
        return None
    media = content.get("application/json")
    if not isinstance(media, dict) or not isinstance(media.get("schema"), dict):
        return None
    return dict(media["schema"])
