from antcode_web_api.app_factory import create_app


def test_operation_ids_are_unique():
    app = create_app()
    schema = app.openapi()

    operation_ids = []
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            operation_id = operation.get("operationId")
            if operation_id:
                operation_ids.append(operation_id)

    assert len(operation_ids) == len(set(operation_ids))


def test_default_error_responses_are_registered():
    app = create_app()
    schema = app.openapi()

    permissions_op = schema["paths"]["/api/v1/auth/permissions"]["get"]
    responses = permissions_op["responses"]

    assert "401" in responses
    assert "403" in responses
    assert "500" in responses
