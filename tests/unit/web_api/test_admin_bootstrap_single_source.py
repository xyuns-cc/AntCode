"""默认管理员只能有一个创建方：``scripts/init_db.py::_create_admin``。

历史形态是两份都活着：``lifespan`` 里一份（email 写死 ``admin@example.com``）、
``init_db`` 里一份（email 取 ``{username}@localhost``）。默认 compose 让 migration
先跑完，lifespan 那份永远走"已存在→跳过"，于是它带的 TOCTOU 被启动顺序掩盖——
一旦 users 被清空再重启 web-api，``SERVER_WORKERS`` 个 uvicorn 子进程同时
check-then-act，输的那个吃 ``users_username_key`` 唯一冲突并被 uvicorn 判定
"Application startup failed" 杀掉。这组用例锁住"只剩一份"。

断言走 AST 而不是全文匹配：本模块的说明性文档里必然会提到这些名字，
文本匹配会把"解释为什么不能这么写"的注释本身判成违规。
"""

import ast
import importlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIFESPAN_SOURCE = REPO_ROOT / "services/web_api/src/antcode_web_api/lifespan.py"
INIT_DB_SOURCE = REPO_ROOT / "scripts/init_db.py"


def _referenced_names(path: Path) -> set[str]:
    """模块里真正被引用到的标识符（属性名 / 变量名 / 导入名），不含注释与 docstring。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.rsplit(".", 1)[-1])
    return names


def _string_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def test_lifespan_exposes_no_admin_bootstrap():
    """web-api 常驻进程不得再有建管理员的入口（把函数加回来这里就红）。"""
    lifespan_module = importlib.import_module("antcode_web_api.lifespan")

    assert not hasattr(lifespan_module, "_create_default_admin")
    assert [name for name in dir(lifespan_module) if "admin" in name.lower()] == []


def test_lifespan_never_reads_the_bootstrap_password():
    """生产刻意不把一次性引导口令给常驻服务，代码里引用到即视为回退。"""
    names = _referenced_names(LIFESPAN_SOURCE)

    assert "DEFAULT_ADMIN_PASSWORD" not in names
    assert "DEFAULT_ADMIN_USERNAME" not in names
    assert "UserCreateRequest" not in names
    assert "user_service" not in names


def test_web_api_never_creates_schema_so_init_db_is_mandatory():
    """删掉 lifespan 那份的前提：任何部署形态都必须先跑 init_db。

    web-api 只 ``Tortoise.init``、从不建表，因此没跑过 init_db 的库连表都没有——
    init_db 必然被执行过，管理员也就必然被创建过。这条断言锁住那个前提。
    """
    names = _referenced_names(LIFESPAN_SOURCE)

    assert "init" in names, "lifespan 应当仍然调用 Tortoise.init"
    assert "generate_schemas" not in names


def test_init_db_is_the_sole_admin_creator_and_derives_email_from_username():
    """保留下来的那份必须仍在，且 email 随用户名走而不是写死一个域名。"""
    tree = ast.parse(INIT_DB_SOURCE.read_text(encoding="utf-8"))
    functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)}

    assert "_create_admin" in functions
    assert "admin@example.com" not in _string_constants(INIT_DB_SOURCE)
    # email 是 f-string（JoinedStr）而非常量，才谈得上"随用户名变"
    assert "{username}@localhost" not in _string_constants(INIT_DB_SOURCE)
    assert "@localhost" in _string_constants(INIT_DB_SOURCE)
