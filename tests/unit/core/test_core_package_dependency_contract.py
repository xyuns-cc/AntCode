import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_core_declares_contracts_runtime_dependency() -> None:
    pyproject = tomllib.loads((ROOT / "packages/antcode_core/pyproject.toml").read_text())
    dependencies = pyproject["project"]["dependencies"]

    assert any(value.startswith("antcode-contracts") for value in dependencies)


def test_core_contracts_dependency_is_locked() -> None:
    lock_text = (ROOT / "uv.lock").read_text()
    core_section = lock_text.split('name = "antcode-core"', 1)[1].split("[[package]]", 1)[0]

    assert '{ name = "antcode-contracts" }' in core_section


def test_scrapy_declares_direct_core_dependency() -> None:
    pyproject = tomllib.loads((ROOT / "packages/antcode_scrapy/pyproject.toml").read_text())
    dependencies = pyproject["project"]["dependencies"]
    sources = pyproject["tool"]["uv"]["sources"]

    assert any(value.startswith("antcode-core") for value in dependencies)
    assert sources["antcode-core"] == {"workspace": True}


def test_master_does_not_install_asyncio_backport() -> None:
    pyproject = tomllib.loads((ROOT / "services/master/pyproject.toml").read_text())
    dependencies = pyproject["project"]["dependencies"]
    lock_text = (ROOT / "uv.lock").read_text()

    assert not any(value.lower().startswith("asyncio") for value in dependencies)
    assert '\nname = "asyncio"\n' not in lock_text
