import pytest

from handoff.audit import AuditLog
from handoff.config import ApprovalConfig, load_settings
from handoff.policy import PolicyEngine
from handoff.snapshots import SnapshotStore
from handoff.tools import FileTools


@pytest.fixture
def sandbox(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    return allowed


@pytest.fixture
def tools(tmp_path, sandbox):
    policy = PolicyEngine([sandbox], ApprovalConfig())
    snapshots = SnapshotStore(tmp_path / "snaps")
    audit = AuditLog(tmp_path / "audit.log")
    return FileTools("job1", policy, snapshots, audit)


@pytest.fixture
def project(tmp_path):
    """A project folder with a config.yaml that uses relative paths."""
    (tmp_path / "workspace").mkdir()
    (tmp_path / "config.yaml").write_text(
        "server:\n  port: 8765\n"
        "agent:\n  allowed_folders: [workspace]\n"
        "llm:\n  provider: demo\n"  # tests must never depend on a real Ollama
        "storage:\n  results_dir: data/results\n  snapshots_dir: data/snapshots\n"
        "logging:\n  audit_file: data/audit.log\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def settings(project):
    return load_settings(project / "config.yaml")


@pytest.fixture
def ro_project(tmp_path):
    """Like `project`, plus a read-only folder `reference` holding a few files."""
    (tmp_path / "workspace").mkdir()
    deep = tmp_path / "reference" / "deep" / "deeper"
    deep.mkdir(parents=True)
    (tmp_path / "reference" / "a.bak").write_text("1", encoding="utf-8")
    (tmp_path / "reference" / "deep" / "B.BAK").write_text("2", encoding="utf-8")
    (deep / "notes.txt").write_text("3", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "server:\n  port: 8765\n"
        "agent:\n  allowed_folders: [workspace]\n  read_only_folders: [reference]\n"
        "llm:\n  provider: demo\n"
        "storage:\n  results_dir: data/results\n  snapshots_dir: data/snapshots\n"
        "logging:\n  audit_file: data/audit.log\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def ro_settings(ro_project):
    return load_settings(ro_project / "config.yaml")


@pytest.fixture(autouse=True)
def _daily_files_stay_in_the_test_folder(monkeypatch, tmp_path):
    """The everyday helpers keep their files in the user's Documents by default: a test must never write there."""
    monkeypatch.setenv("HANDOFF_DAILY__FOLDER", str(tmp_path / "daily-files"))


@pytest.fixture(autouse=True)
def _shared_token_unless_a_test_asks_for_accounts(monkeypatch):
    """Most tests are about files and jobs and use the one shared token; the account tests turn real accounts on."""
    monkeypatch.setenv("HANDOFF_ACCOUNTS__ENABLED", "false")
