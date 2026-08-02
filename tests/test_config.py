from pathlib import Path

from llm_wiki.config import VaultSettings


def test_load_defaults_when_no_config_file(tmp_path: Path):
    settings = VaultSettings.load(tmp_path)
    assert settings.vault_root == tmp_path
    assert settings.db_path == tmp_path / ".llm-wiki" / "db.sqlite3"
    assert settings.wiki_dir == tmp_path / "wiki"
    assert settings.auto_watch_raw is False


def test_save_then_load_round_trip(tmp_path: Path):
    settings = VaultSettings.load(tmp_path)
    settings.auto_watch_raw = True
    settings.lint_on_save = False
    settings.save()

    assert settings.config_path.exists()

    reloaded = VaultSettings.load(tmp_path)
    assert reloaded.auto_watch_raw is True
    assert reloaded.lint_on_save is False
