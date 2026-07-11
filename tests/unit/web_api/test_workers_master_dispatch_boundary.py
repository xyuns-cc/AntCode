from antcode_web_api.routes.v1 import workers as workers_route


def _registered_paths() -> set[str]:
    return {route.path for route in workers_route.router.routes}


def test_web_api_registers_authorized_dispatch_routes():
    expected_paths = {
        "/dispatch/task",
        "/dispatch/batch",
        "/dispatch/queue/{worker_id}/status",
        "/dispatch/queue/{worker_id}/tasks/{task_id}/priority",
        "/dispatch/queue/{worker_id}/tasks/{task_id}",
        "/dispatch/task/{worker_id}/{task_id}/status",
        "/dispatch/task/{worker_id}/{task_id}/logs",
    }

    assert expected_paths <= _registered_paths()


def test_web_api_exports_strict_dispatch_request_models():
    assert workers_route.WorkerDispatchTaskRequest.model_config["extra"] == "forbid"
    assert workers_route.WorkerDispatchBatchRequest.model_config["extra"] == "forbid"
