from pathlib import Path

import pytest
from antcode_core.application.services.projects import source_bundle_errors as bundle_errors
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


def test_bundle_rejects_symlink_instead_of_dropping_it(tmp_path):
    """符号链接必须在生产侧带码报错，不能静默丢弃后在 Worker 侧炸一句无关的话。

    npm 的 ``node_modules/.bin/*`` 永远是符号链接，旧实现直接跳过它们，于是
    "把 node_modules 提交进仓库"必然失败，而报错一个字都不提符号链接。
    """
    repo = tmp_path / "repo"
    project = repo / "app"
    runner_dir = project / "node_modules" / ".bin"
    runner_dir.mkdir(parents=True)
    (project / "main.ts").write_text("export const a = 1;\n", encoding="utf-8")
    (project / "node_modules" / "tsx.js").write_text("// runner\n", encoding="utf-8")
    (runner_dir / "tsx").symlink_to(Path("..") / "tsx.js")

    with pytest.raises(bundle_errors.SourceBundleRejected) as excinfo:
        path_module.resolve_bundle_paths(repo, subdir="app", entry_point="main.ts", include_paths=[])

    assert excinfo.value.error_code == bundle_errors.SOURCE_BUNDLE_SYMLINK_REJECTED
    assert "app/node_modules/.bin/tsx" in excinfo.value.detail


def test_bundle_rejects_symlinked_directory(tmp_path):
    """os.walk 不跟随目录软链，目录形态同样是"静默少一整棵子树"。"""
    repo = tmp_path / "repo"
    project = repo / "app"
    shared = repo / "shared"
    shared.mkdir(parents=True)
    project.mkdir(parents=True)
    (shared / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "main.py").write_text("print(1)\n", encoding="utf-8")
    (project / "vendor").symlink_to(shared, target_is_directory=True)

    with pytest.raises(bundle_errors.SourceBundleRejected) as excinfo:
        path_module.resolve_bundle_paths(repo, subdir="app", entry_point="main.py", include_paths=[])

    assert excinfo.value.error_code == bundle_errors.SOURCE_BUNDLE_SYMLINK_REJECTED
    assert "app/vendor" in excinfo.value.detail


def test_bundle_rejects_unmaterialized_git_lfs_pointer(tmp_path):
    pointer = tmp_path / "model.bin"
    pointer.write_text(
        f"version https://git-lfs.github.com/spec/v1\noid sha256:{'a' * 64}\nsize 123456\n",
        encoding="ascii",
    )

    with pytest.raises(ValueError, match="Git LFS 文件未物化"):
        path_module.validate_bundle_paths([pointer])


def test_bundle_rejects_crlf_git_lfs_pointer(tmp_path):
    pointer = tmp_path / "model.bin"
    pointer.write_bytes(
        b"version https://git-lfs.github.com/spec/v1\r\n"
        + f"oid sha256:{'b' * 64}\r\n".encode("ascii")
        + b"size 99\r\n"
    )

    with pytest.raises(ValueError, match="Git LFS 文件未物化"):
        path_module.validate_bundle_paths([pointer])


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


@pytest.mark.parametrize("absolute", ["/etc", "/etc/passwd", "//etc", "\\\\etc", " /etc "])
def test_absolute_paths_are_rejected_instead_of_silently_relativized(absolute):
    """`strip("/")` 曾把 "/etc" 静默改写成仓库内的 "etc"，绝对路径必须显式报错。"""
    with pytest.raises(ValueError, match="必须是相对路径"):
        path_module.normalize_relative_path(absolute, field_name="include_paths")


def test_include_paths_reject_absolute_entries():
    with pytest.raises(ValueError, match="必须是相对路径"):
        path_module.string_list(["libs/common", "/etc"])


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


@pytest.mark.parametrize("marker", ["requirements.txt", "pyproject.toml"])
def test_repository_scan_ignores_dependency_only_directories(tmp_path, marker):
    repo = tmp_path / "repo"
    dependency_dir = repo / "libs" / "dependency-only"
    executable_dir = repo / "spiders" / "news"
    dependency_dir.mkdir(parents=True)
    executable_dir.mkdir(parents=True)
    (dependency_dir / marker).write_text("", encoding="utf-8")
    (executable_dir / "main.py").write_text("print('news')\n", encoding="utf-8")
    (executable_dir / "requirements.txt").write_text("httpx\n", encoding="utf-8")

    candidates = path_module.scan_repository_candidates(repo)

    assert candidates == [
        {
            "subdir": "spiders/news",
            "entry_point": "main.py",
            "markers": ["main.py", "requirements.txt"],
        }
    ]


def test_repository_scan_rejects_file_count_over_limit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("", encoding="utf-8")
    (repo / "b.py").write_text("", encoding="utf-8")
    limits = path_module.RepositoryScanLimits(max_files=1, max_directories=10, max_depth=10, max_candidates=10)

    with pytest.raises(ValueError, match="文件数超过上限"):
        path_module.scan_repository_candidates(repo, limits)


def test_repository_scan_rejects_candidate_count_over_limit(tmp_path):
    repo = tmp_path / "repo"
    for name in ("one", "two"):
        project = repo / name
        project.mkdir(parents=True)
        (project / "main.py").write_text("", encoding="utf-8")
    limits = path_module.RepositoryScanLimits(max_files=10, max_directories=10, max_depth=10, max_candidates=1)

    with pytest.raises(ValueError, match="候选项目数超过上限"):
        path_module.scan_repository_candidates(repo, limits)
