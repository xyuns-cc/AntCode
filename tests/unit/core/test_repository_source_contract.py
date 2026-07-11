from pathlib import Path

import pytest
from antcode_core.application.services.projects import source_bundle_paths as path_module
from antcode_core.domain.models import (
    GitRepository,
    ProjectSource,
    RunSourceSnapshot,
    SourceArtifact,
    SourceArtifactChunk,
)


def test_production_source_models_are_registered():
    assert GitRepository.Meta.table == "git_repositories"
    assert ProjectSource.Meta.table == "project_sources"
    assert SourceArtifact.Meta.table == "source_artifacts"
    assert SourceArtifactChunk.Meta.table == "source_artifact_chunks"
    assert RunSourceSnapshot.Meta.table == "run_source_snapshots"


def test_project_source_does_not_mirror_project_runtime_fields():
    fields = set(ProjectSource._meta.fields_map)

    assert "entry_point" not in fields
    assert "runtime_config" not in fields


def test_bundle_keeps_repo_relative_paths_and_explicit_includes(tmp_path):
    repo = tmp_path / "repo"
    (repo / "libs" / "common").mkdir(parents=True)
    (repo / "spiders" / "news").mkdir(parents=True)
    (repo / "spiders" / "shop").mkdir(parents=True)
    (repo / "libs" / "common" / "helpers.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "spiders" / "news" / "main.py").write_text("print('news')\n", encoding="utf-8")
    (repo / "spiders" / "shop" / "main.py").write_text("print('shop')\n", encoding="utf-8")

    paths = path_module.resolve_bundle_paths(
        repo,
        subdir="spiders/news",
        entry_point="main.py",
        include_paths=["libs/common"],
    )
    content = path_module.create_deterministic_tar_gz(repo, paths)

    names = path_module.list_tar_names(content)

    assert names == ["libs/common/helpers.py", "spiders/news/main.py"]


def test_entry_point_must_stay_inside_subdir(tmp_path):
    repo = tmp_path / "repo"
    (repo / "spiders" / "news").mkdir(parents=True)
    (repo / "spiders" / "shop").mkdir(parents=True)
    (repo / "spiders" / "shop" / "main.py").write_text("print('shop')\n", encoding="utf-8")

    try:
        path_module.resolve_bundle_paths(
            repo,
            subdir="spiders/news",
            entry_point="../shop/main.py",
            include_paths=[],
        )
    except ValueError as exc:
        assert "入口文件" in str(exc)
    else:
        raise AssertionError("entry point outside subdir must fail")


@pytest.mark.parametrize("subdir", [None, "", ".", " / "])
def test_project_source_subdir_must_be_explicit(subdir):
    with pytest.raises(ValueError, match="Git 子目录"):
        path_module.normalize_source_subdir(subdir)


def test_repository_scan_detects_candidate_subdirectories(tmp_path):
    repo = tmp_path / "repo"
    (repo / "libs" / "common").mkdir(parents=True)
    (repo / "spiders" / "news").mkdir(parents=True)
    (repo / "spiders" / "shop").mkdir(parents=True)
    (repo / "spiders" / "news" / "main.py").write_text("print('news')\n", encoding="utf-8")
    (repo / "spiders" / "shop" / "requirements.txt").write_text("httpx\n", encoding="utf-8")
    (repo / "spiders" / "shop" / "spider.py").write_text("print('shop')\n", encoding="utf-8")

    candidates = path_module.scan_repository_candidates(repo)

    assert [item["subdir"] for item in candidates] == ["spiders/news", "spiders/shop"]
    assert candidates[0]["entry_point"] == "main.py"
    assert candidates[1]["entry_point"] == "spider.py"
