from pathlib import Path


def test_init_db_script_uses_current_tortoise_config():
    source = Path("scripts/init_db.py").read_text(encoding="utf-8")

    assert "src.core.db_config" not in source
    assert "Tortoise.generate_schemas" in source
    assert 'init_db(config=config, service="web_api")' in source
    assert "REQUIRED_TABLES" in source
