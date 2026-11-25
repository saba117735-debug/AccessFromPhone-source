import pytest
from pydantic import ValidationError

from handoff.audit import AuditLog
from handoff.config import Action, ApprovalConfig, LlmConfig, load_settings
from handoff.jobs import JobManager, Status
from handoff.planner import OllamaPlanner
from handoff.policy import PathDenied, PolicyEngine, ReadOnlyPath
from handoff.snapshots import SnapshotStore
from handoff.tools import MAX_MATCHES, FileTools, name_matcher


@pytest.fixture
def world(tmp_path):
    rw, ro = tmp_path / "rw", tmp_path / "ro"
    (ro / "deep" / "deeper").mkdir(parents=True)
    rw.mkdir()
    (ro / "a.bak").write_text("1", encoding="utf-8")
    (ro / "deep" / "B.BAK").write_text("2", encoding="utf-8")
    (ro / "deep" / "deeper" / "old.bak.txt").write_text("3", encoding="utf-8")
    (ro / "deep" / "deeper" / "plain.txt").write_text("4", encoding="utf-8")
    policy = PolicyEngine([rw], ApprovalConfig(), [ro])
    tools = FileTools("job", policy, SnapshotStore(tmp_path / "snaps"), AuditLog(tmp_path / "audit.log"))
    return {"rw": rw, "ro": ro, "policy": policy, "tools": tools, "root": tmp_path}


# --- policy -----------------------------------------------------------------

def test_read_only_folder_can_be_read(world):
    assert world["tools"].read_file(world["ro"] / "a.bak") == "1"


def test_read_only_folder_cannot_be_changed_even_when_approved(world):
    tools, ro = world["tools"], world["ro"]
    with pytest.raises(ReadOnlyPath):
        tools.write_file(ro / "new.txt", "x", approved=True)
    with pytest.raises(ReadOnlyPath):
        tools.delete_file(ro / "a.bak", approved=True)
    assert not (ro / "new.txt").exists() and (ro / "a.bak").exists()


def test_read_only_error_is_also_a_path_denied(world):
    with pytest.raises(PathDenied, match="read-only"):
        world["policy"].resolve(world["ro"] / "a.bak", for_write=True)


def test_writable_folder_still_works_and_outside_is_still_denied(world):
    world["tools"].write_file(world["rw"] / "ok.txt", "x", approved=True)
    with pytest.raises(PathDenied):
        world["tools"].read_file(world["root"] / "elsewhere.txt")


def test_dot_dot_out_of_a_read_only_folder_is_denied(world):
    (world["root"] / "secret.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(PathDenied):
        world["tools"].read_file(world["ro"] / ".." / "secret.txt")


# --- search -----------------------------------------------------------------

def names(result):
    return sorted(p.name for p in result.matches)


def test_find_files_searches_every_sub_folder_ignoring_case(world):
    assert names(world["tools"].find_files(world["ro"], "*.bak")) == ["B.BAK", "a.bak"]


def test_extension_and_plain_word_shortcuts(world):
    tools, ro = world["tools"], world["ro"]
    assert names(tools.find_files(ro, ".bak")) == ["B.BAK", "a.bak"]
    assert names(tools.find_files(ro, "plain")) == ["plain.txt"]


def test_no_matches_says_so(world):
    text = world["tools"].find_files(world["ro"], "*.zip").render()
    assert "no matching files" in text and str(world["ro"]) in text


def test_bad_patterns_are_refused(world):
    for bad in ("", "  ", "sub\\*.bak", "a/b"):
        with pytest.raises(ValueError):
            world["tools"].find_files(world["ro"], bad)


def test_search_is_limited_to_the_allowed_folders(world):
    with pytest.raises(PathDenied):
        world["tools"].find_files(world["root"], "*.bak")


def test_search_stops_at_the_match_limit(world):
    for i in range(MAX_MATCHES + 5):
        (world["rw"] / f"f{i}.dat").write_text("x", encoding="utf-8")
    result = world["tools"].find_files(world["rw"], "*.dat")
    assert len(result.matches) == MAX_MATCHES and result.hit_match_limit
    assert "stopped after" in result.render()


def test_node_modules_and_git_are_skipped_and_reported(world):
    for junk in ("node_modules", ".git"):
        (world["ro"] / junk).mkdir()
        (world["ro"] / junk / "x.bak").write_text("x", encoding="utf-8")
    result = world["tools"].find_files(world["ro"], "*.bak")
    assert names(result) == ["B.BAK", "a.bak"]
    assert "skipped folders: .git, node_modules" in result.render()


def test_search_is_audited(world):
    world["tools"].find_files(world["ro"], "*.bak")
    assert '"find_files"' in AuditLog(world["root"] / "audit.log").as_text()


def test_name_matcher_basics():
    assert name_matcher("*.BAK")("report.bak")
    assert name_matcher("report?.txt")("report1.txt")
    assert not name_matcher("*.bak")("report.txt")


# --- config -----------------------------------------------------------------

def test_read_only_folders_resolve_against_the_config_file(ro_settings, ro_project):
    assert ro_settings.agent.read_only_folders == [(ro_project / "reference").resolve()]


def test_missing_read_only_folder_is_a_clear_error(ro_project):
    cfg = ro_project / "config.yaml"
    cfg.write_text(cfg.read_text().replace("[reference]", "[nope]"), encoding="utf-8")
    with pytest.raises(ValidationError, match="read-only folder does not exist"):
        load_settings(cfg)


def test_read_only_inside_writable_is_rejected(ro_project):
    (ro_project / "workspace" / "inner").mkdir()
    cfg = ro_project / "config.yaml"
    cfg.write_text(cfg.read_text().replace("[reference]", "[workspace/inner]"), encoding="utf-8")
    with pytest.raises(ValidationError, match="inside the writable folder"):
        load_settings(cfg)


def test_read_only_folders_default_to_none(settings):
    assert settings.agent.read_only_folders == []


# --- jobs (demo planner) ----------------------------------------------------

def test_find_command_over_a_read_only_folder(ro_settings, ro_project):
    ref = ro_settings.agent.read_only_folders[0]
    job = JobManager(ro_settings).submit(f"find *.bak in {ref}")
    assert job.status is Status.DONE
    assert "a.bak" in job.outputs[0] and "B.BAK" in job.outputs[0]
    assert job.steps[0].detail() == "find *.bak"


def test_changing_something_in_a_read_only_folder_fails_before_approval(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    job = JobManager(ro_settings).submit(f"create {ref}\\x.txt with hi")
    assert job.status is Status.FAILED and "read-only" in job.error
    assert not (ref / "x.txt").exists()


def test_deleting_from_a_read_only_folder_fails_before_approval(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    job = JobManager(ro_settings).submit(f"delete {ref}\\a.bak")
    assert job.status is Status.FAILED and (ref / "a.bak").exists()


def test_reading_a_read_only_file_needs_no_approval(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    job = JobManager(ro_settings).submit(f"read {ref}\\a.bak")
    assert job.status is Status.DONE and job.outputs == ["1"]


# --- the model uses the search tool -----------------------------------------

class Script:
    def __init__(self, *messages):
        self.messages, self.requests = list(messages), []

    def __call__(self, url, payload, timeout):
        import copy

        self.requests.append(copy.deepcopy(payload))
        return {"message": self.messages.pop(0)}


def test_model_searches_a_read_only_folder_through_the_real_manager(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    llm = LlmConfig(provider="ollama")
    script = Script(
        {"tool_calls": [{"function": {"name": "find_files", "arguments": {"pattern": "*.bak", "path": str(ref)}}}]},
        {"content": "Yes, two .bak files."},
    )
    manager = JobManager(ro_settings, OllamaPlanner(llm, script))
    job = manager.submit("do we have .bak files?")
    manager.join_background()  # the model plans in a background thread
    assert job.status is Status.DONE and job.outputs == ["Yes, two .bak files."]
    found = script.requests[1]["messages"][-1]["content"]  # what the search tool gave the model
    assert "a.bak" in found and "B.BAK" in found


def test_system_prompt_names_the_folders_and_marks_read_only(ro_settings):
    ref = ro_settings.agent.read_only_folders[0]
    llm = LlmConfig(provider="ollama")
    script = Script({"content": "ok"})
    manager = JobManager(ro_settings, OllamaPlanner(llm, script))
    manager.submit("hi")
    manager.join_background()
    system = script.requests[0]["messages"][0]["content"]
    assert f"{ref} (READ-ONLY)" in system
    assert "read and write; relative paths start here" in system
