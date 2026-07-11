from antcode_core.application.services.id_service import IdService


def test_generate_run_id_default_prefix():
    run_id = IdService.generate_run_id()
    assert run_id.startswith("run-")


def test_generate_run_id_with_custom_prefix():
    run_id = IdService.generate_run_id("custom")
    assert run_id.startswith("custom-")
