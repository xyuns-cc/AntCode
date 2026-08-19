"""RuntimeKind 的 JAVA / GO 是三重死的语言真源，必须彻底消失。

它们没有任何写入方：``routes/v1/project.py`` 组装 ProjectCreateRequest 时根本不拷贝
``runtime_kind``，``project_runtime_binding`` 与仓库导入都硬拒非 python，Worker 侧
零引用。留着的唯一效果是：POST 一个 ``runtime_kind=java`` 会返回 200 并静默建成
python 项目，同时给"这个项目用什么语言"留下第二个可分叉的真源（第一个是
``project_codes.language``，由 execution_language 契约承载）。
"""

import pytest
from antcode_core.domain.models.enums import ProjectType, RuntimeKind, RuntimeScope
from antcode_core.domain.schemas.project import ProjectCreateFormRequest
from pydantic import ValidationError


def test_runtime_kind_has_python_only():
    assert [kind.value for kind in RuntimeKind] == ["python"]


def test_project_create_form_rejects_non_python_runtime_kind_instead_of_silently_downgrading():
    with pytest.raises(ValidationError):
        ProjectCreateFormRequest(
            name="java-project",
            type=ProjectType.CODE,
            runtime_scope=RuntimeScope.PRIVATE,
            runtime_kind="java",
        )


def test_project_create_form_still_accepts_python():
    form = ProjectCreateFormRequest(
        name="py-project",
        type=ProjectType.CODE,
        runtime_scope=RuntimeScope.PRIVATE,
        runtime_kind="python",
    )

    assert form.runtime_kind is RuntimeKind.PYTHON
