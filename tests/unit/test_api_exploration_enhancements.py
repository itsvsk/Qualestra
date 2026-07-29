from sentinelqa.execution.schemathesis import response_schema, safe_operations


def test_safe_operations_are_read_only_and_impact_prioritized() -> None:
    document = {
        "openapi": "3.1.0",
        "paths": {
            "/projects": {"get": {"operationId": "list_projects"}},
            "/billing/plans": {
                "get": {"operationId": "get_plans"},
                "post": {"operationId": "create_plan"},
            },
        },
    }
    operations = safe_operations(document, features=["billing"])
    assert [(method, path) for method, path, _ in operations] == [
        ("GET", "/billing/plans"),
        ("GET", "/projects"),
    ]


def test_response_schema_selects_status_then_default() -> None:
    operation = {
        "responses": {
            "200": {"content": {"application/json": {"schema": {"type": "array"}}}},
            "default": {"content": {"application/json": {"schema": {"type": "object"}}}},
        }
    }
    assert response_schema(operation, 200) == {"type": "array"}
    assert response_schema(operation, 503) == {"type": "object"}
