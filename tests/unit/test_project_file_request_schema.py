from antcode_core.domain.schemas.project import ProjectFileCreateRequest, ProjectFileUpdateRequest


def test_file_create_blank_json_fields_become_empty_dicts():
    request = ProjectFileCreateRequest(
        name="Demo File",
        type="file",
        worker_id="worker-1",
        runtime_scope="shared",
        python_version="3.12.11",
        repository_id="repo-001",
        subdir="spiders/news",
        entry_point="main.py",
        runtime_config="   ",
        environment_vars="",
    )

    assert request.runtime_config == {}
    assert request.environment_vars == {}


def test_file_create_accepts_worker_environment_name():
    request = ProjectFileCreateRequest(
        name="Demo File",
        type="file",
        worker_id="worker-1",
        runtime_scope="private",
        python_version="3.12.11",
        env_name="project-demo-py31211",
        env_description="Project runtime",
        repository_id="repo-001",
        subdir="spiders/news",
        entry_point="main.py",
    )

    assert request.env_name == "project-demo-py31211"
    assert request.env_description == "Project runtime"


def test_file_update_blank_json_fields_become_empty_dicts():
    request = ProjectFileUpdateRequest(runtime_config="   ", environment_vars="")

    assert request.runtime_config == {}
    assert request.environment_vars == {}
