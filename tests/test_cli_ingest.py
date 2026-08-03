from pathlib import Path

import pygit2
import pytest
from typer.testing import CliRunner

import llm_wiki.cli as cli_module
from llm_wiki.cli import app
from llm_wiki.llm.client import ExtractionResult
from llm_wiki.vault import VaultManager

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch):
    """`ingest step`/`run` always construct a real `LlamaClient` now
    (INGEST_PLAN.md §10) -- just constructing `openai.OpenAI(...)`
    consults proxy env vars, and this sandbox sets a global SOCKS proxy
    that httpx can't use without the optional `socksio` package. That's
    a sandbox artifact, not something LlamaClient's own correctness
    should depend on -- clear it for every test in this file."""
    for var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)


class FakeLlmClient:
    """Trivial `LlmClient` double so real `compile()`/`cascade()` can
    both succeed without a live `llama-server` -- see
    `with_fake_llm_client` below. `embed()` needs real-shaped (if fake)
    vectors now that `cascade()` is real too (INGEST_PLAN.md §11) and
    calls it while writing a source note -- 768-wide to match
    `DEFAULT_EMBEDDING_DIM`, which is what `vault_root`'s `StorageEngine`
    actually created `vec_chunks` with (a mismatched width would be a
    real `sqlite-vec` dimension error, not just a fake-data nitpick)."""

    def summarize(self, text: str) -> str:
        return "a summary"

    def extract(self, text: str) -> ExtractionResult:
        return ExtractionResult(entities=[], concepts=[])

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 768 for _ in texts]


@pytest.fixture
def with_fake_llm_client(monkeypatch):
    """Monkeypatches `llm_wiki.cli.LlamaClient` (what `_dispatch_table()`
    constructs) so `ingest step`/`run` drive real `compile()`/`cascade()`
    against a fake client instead of trying to reach an actual
    `llama-server`."""
    monkeypatch.setattr(cli_module, "LlamaClient", lambda config: FakeLlmClient())


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    manager = VaultManager(recent_vaults_path=tmp_path / "_recent" / "recent_vaults.json")
    settings = manager.create(tmp_path / "vault")
    return settings.vault_root


def _write(tmp_path: Path, name: str, content: str = "hello") -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def _add(vault_root: Path, path: Path):
    return runner.invoke(app, ["ingest", "add", str(path), "--vault", str(vault_root)])


# -- add ---------------------------------------------------------------


def test_add_stages_file(tmp_path: Path, vault_root: Path):
    source = _write(tmp_path, "note.md", "# heading\ncontent")

    result = _add(vault_root, source)

    assert result.exit_code == 0
    assert "STAGED" in result.stdout


def test_add_missing_file_fails(tmp_path: Path, vault_root: Path):
    result = _add(vault_root, tmp_path / "does-not-exist.md")

    assert result.exit_code == 1
    assert "FAILED" in result.stdout


# -- list / status -------------------------------------------------------


def test_list_shows_staged_item(tmp_path: Path, vault_root: Path):
    _add(vault_root, _write(tmp_path, "note.md"))

    result = runner.invoke(app, ["ingest", "list", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "note" in result.stdout
    assert "STAGED" in result.stdout


def test_list_status_filter(tmp_path: Path, vault_root: Path, with_fake_llm_client):
    _add(vault_root, _write(tmp_path, "good.txt"))
    _add(vault_root, _write(tmp_path, "bad.pdf"))
    # Drive both as far as they'll go -- "bad.pdf" isn't a supported
    # format (fails in atomize()); "good.txt" reaches COMPLETED via the
    # fake LLM client (compile() + cascade(), §10/§11).
    runner.invoke(app, ["ingest", "run", "--count", "AUTO", "--vault", str(vault_root)])

    result = runner.invoke(app, ["ingest", "list", "--status", "FAILED", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "bad" in result.stdout
    assert "good" not in result.stdout


def test_status_shows_detail(tmp_path: Path, vault_root: Path):
    _add(vault_root, _write(tmp_path, "note.md"))

    result = runner.invoke(app, ["ingest", "status", "1", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "raw_path" in result.stdout
    assert "archive_path" in result.stdout


def test_status_missing_id_fails(vault_root: Path):
    result = runner.invoke(app, ["ingest", "status", "999", "--vault", str(vault_root)])

    assert result.exit_code == 1


# -- step ----------------------------------------------------------------


def test_step_single_id_advances_one_step(tmp_path: Path, vault_root: Path):
    _add(vault_root, _write(tmp_path, "note.md"))

    result = runner.invoke(app, ["ingest", "step", "1", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "STAGED" in result.stdout
    assert "QUEUED" in result.stdout


def test_step_count_batch_filtered_by_status(tmp_path: Path, vault_root: Path):
    _add(vault_root, _write(tmp_path, "one.txt"))
    _add(vault_root, _write(tmp_path, "two.txt"))

    result = runner.invoke(
        app, ["ingest", "step", "--count", "5", "--status", "STAGED", "--vault", str(vault_root)]
    )

    assert result.exit_code == 0
    list_result = runner.invoke(app, ["ingest", "list", "--vault", str(vault_root)])
    assert list_result.stdout.count("QUEUED") == 2


def test_step_requires_exactly_one_of_id_or_count(vault_root: Path):
    neither = runner.invoke(app, ["ingest", "step", "--vault", str(vault_root)])
    both = runner.invoke(app, ["ingest", "step", "1", "--count", "5", "--vault", str(vault_root)])

    assert neither.exit_code == 1
    assert both.exit_code == 1


# -- run -------------------------------------------------------------------


def test_run_explicit_id_to_completion(tmp_path: Path, vault_root: Path, with_fake_llm_client):
    _add(vault_root, _write(tmp_path, "note.txt"))

    result = runner.invoke(app, ["ingest", "run", "1", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "COMPLETED" in result.stdout  # cascade() now runs for real (§11)
    assert "Committed" in result.stdout


def test_run_count_auto_drains_pool(tmp_path: Path, vault_root: Path, with_fake_llm_client):
    _add(vault_root, _write(tmp_path, "one.txt"))
    _add(vault_root, _write(tmp_path, "two.txt"))

    result = runner.invoke(app, ["ingest", "run", "--count", "AUTO", "--vault", str(vault_root)])

    assert result.exit_code == 0
    list_result = runner.invoke(app, ["ingest", "list", "--vault", str(vault_root)])
    assert list_result.stdout.count("COMPLETED") == 2


def test_run_stops_on_failure(tmp_path: Path, vault_root: Path):
    _add(vault_root, _write(tmp_path, "good.txt"))
    _add(vault_root, _write(tmp_path, "bad.pdf"))  # unsupported format
    _add(vault_root, _write(tmp_path, "third.txt"))

    result = runner.invoke(app, ["ingest", "run", "--count", "AUTO", "--vault", str(vault_root)])

    assert result.exit_code == 1
    assert "FAILED" in result.stdout
    assert "Stopped" in result.stderr  # error-ish summary goes to stderr, not stdout
    # third.txt (id 3) must not have been touched -- the run stopped at bad.pdf (id 2).
    status_result = runner.invoke(app, ["ingest", "status", "3", "--vault", str(vault_root)])
    assert "STAGED" in status_result.stdout


def test_run_commits_completed_items(tmp_path: Path, vault_root: Path, with_fake_llm_client):
    _add(vault_root, _write(tmp_path, "note.txt"))

    result = runner.invoke(app, ["ingest", "run", "--count", "AUTO", "--vault", str(vault_root)])

    assert result.exit_code == 0
    assert "Committed" in result.stdout
    repo = pygit2.Repository(str(vault_root))
    commits = list(repo.walk(repo.head.target))
    assert len(commits) == 1
    assert commits[0].message == "ingest: note"


def test_run_commit_message_lists_multiple_titles(tmp_path: Path, vault_root: Path, with_fake_llm_client):
    _add(vault_root, _write(tmp_path, "one.txt"))
    _add(vault_root, _write(tmp_path, "two.txt"))

    result = runner.invoke(app, ["ingest", "run", "--count", "AUTO", "--vault", str(vault_root)])

    assert result.exit_code == 0
    repo = pygit2.Repository(str(vault_root))
    commits = list(repo.walk(repo.head.target))
    assert len(commits) == 1
    assert commits[0].message == "ingest: 2 files (one, two)"


def test_run_commits_only_items_completed_before_failure(
    tmp_path: Path, vault_root: Path, with_fake_llm_client
):
    _add(vault_root, _write(tmp_path, "good.txt"))
    _add(vault_root, _write(tmp_path, "bad.pdf"))  # unsupported format -> FAILED
    _add(vault_root, _write(tmp_path, "third.txt"))

    result = runner.invoke(app, ["ingest", "run", "--count", "AUTO", "--vault", str(vault_root)])

    assert result.exit_code == 1
    repo = pygit2.Repository(str(vault_root))
    commits = list(repo.walk(repo.head.target))
    assert len(commits) == 1
    assert commits[0].message == "ingest: good"  # not "third" -- never attempted


def test_run_fails_cleanly_with_no_llama_server_reachable(tmp_path: Path, vault_root: Path):
    # No with_fake_llm_client here -- a real LlamaClient, pointed at the
    # vault's default (nothing actually listening) llama.base_url. Once
    # advance() reaches PARSED, real compile() tries a real HTTP call
    # and gets a connection error -- confirms that comes back as a
    # clean FAILED/ANALYZING rather than an unhandled exception/traceback.
    _add(vault_root, _write(tmp_path, "note.txt"))

    result = runner.invoke(app, ["ingest", "run", "1", "--vault", str(vault_root)])

    assert result.exit_code == 1
    assert "FAILED" in result.stdout
    status_result = runner.invoke(app, ["ingest", "status", "1", "--vault", str(vault_root)])
    assert "ANALYZING" in status_result.stdout  # failed_at_step


def test_status_shows_analysis_once_analyzed(tmp_path: Path, vault_root: Path, with_fake_llm_client):
    _add(vault_root, _write(tmp_path, "note.txt"))
    runner.invoke(app, ["ingest", "run", "1", "--vault", str(vault_root)])

    result = runner.invoke(app, ["ingest", "status", "1", "--vault", str(vault_root)])

    assert "summary" in result.stdout
    assert "a summary" in result.stdout


def test_run_invalid_count_fails(vault_root: Path):
    result = runner.invoke(app, ["ingest", "run", "--count", "not-a-number", "--vault", str(vault_root)])

    assert result.exit_code == 1


def test_run_requires_exactly_one_of_ids_or_count(vault_root: Path):
    neither = runner.invoke(app, ["ingest", "run", "--vault", str(vault_root)])

    assert neither.exit_code == 1
