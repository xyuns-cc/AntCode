from pathlib import Path

import antcode_core.common.security as security


def test_security_import_has_one_package_implementation() -> None:
    package_init = Path(security.__file__).resolve()

    assert package_init.name == "__init__.py"
    assert not package_init.parent.with_suffix(".py").exists()
