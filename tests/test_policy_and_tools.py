import pytest

from handoff.audit import AuditLog
from handoff.policy import ApprovalRequired, PathDenied


def test_read_inside_allowed_folder(tools, sandbox):
    (sandbox / "a.txt").write_text("hello", encoding="utf-8")
    assert tools.read_file(sandbox / "a.txt") == "hello"
    assert tools.list_dir(sandbox) == ["a.txt"]


def test_read_outside_is_denied(tools, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    with pytest.raises(PathDenied):
        tools.read_file(outside)


def test_dot_dot_cannot_escape(tools, sandbox, tmp_path):
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    with pytest.raises(PathDenied):
        tools.read_file(sandbox / ".." / "secret.txt")


def test_write_without_approval_is_blocked_and_changes_nothing(tools, sandbox):
    target = sandbox / "new.txt"
    with pytest.raises(ApprovalRequired):
        tools.write_file(target, "x")
    assert not target.exists()


def test_approved_write_then_undo_removes_new_file(tools, sandbox):
    target = sandbox / "new.txt"
    tools.write_file(target, "x", approved=True)
    assert target.read_text(encoding="utf-8") == "x"
    assert tools.undo() == 1
    assert not target.exists()


def test_undo_restores_original_content(tools, sandbox):
    target = sandbox / "doc.txt"
    target.write_text("original", encoding="utf-8")
    tools.write_file(target, "changed", approved=True)
    tools.write_file(target, "changed again", approved=True)
    tools.undo()
    assert target.read_text(encoding="utf-8") == "original"


def test_delete_needs_approval_and_is_undoable(tools, sandbox):
    target = sandbox / "keep.txt"
    target.write_text("data", encoding="utf-8")
    with pytest.raises(ApprovalRequired):
        tools.delete_file(target)
    tools.delete_file(target, approved=True)
    assert not target.exists()
    tools.undo()
    assert target.read_text(encoding="utf-8") == "data"


def test_every_action_is_audited(tools, sandbox, tmp_path):
    tools.write_file(sandbox / "a.txt", "x", approved=True)
    log = AuditLog(tmp_path / "audit.log").as_text().splitlines()
    assert len(log) == 1 and '"write_file"' in log[0]
