from antcode_web_api.routes.v1.tasks import tasks_router


def test_single_segment_static_task_routes_precede_task_id_route() -> None:
    get_paths = [route.path for route in tasks_router.routes if "GET" in getattr(route, "methods", set())]
    dynamic_index = get_paths.index("/{task_id}")

    for static_path in ("/templates", "/running", "/stats"):
        assert get_paths.index(static_path) < dynamic_index
