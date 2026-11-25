from datetime import datetime, timedelta, timezone

import pytest

from handoff.config import Action
from handoff.jobs import JobManager, JobStateError, Status
from handoff.planner import Plan, Step


@pytest.fixture
def manager(settings):
    return JobManager(settings)


@pytest.fixture
def workspace(settings):
    return settings.agent.allowed_folders[0]


def test_read_only_job_runs_without_approval(manager, workspace):
    (workspace / "a.txt").write_text("hello", encoding="utf-8")
    job = manager.submit("list")
    assert job.status is Status.DONE
    assert job.outputs == ["a.txt"]


def test_write_waits_for_approval_then_runs_then_undoes(manager, workspace):
    job = manager.submit("create note.txt with buy milk")
    assert job.status is Status.AWAITING
    assert not (workspace / "note.txt").exists()

    manager.approve(job.id)
    assert job.status is Status.DONE
    assert (workspace / "note.txt").read_text(encoding="utf-8") == "buy milk"

    manager.undo(job.id)
    assert job.status is Status.UNDONE
    assert not (workspace / "note.txt").exists()


def test_reject_changes_nothing(manager, workspace):
    job = manager.submit("create note.txt with x")
    manager.reject(job.id)
    assert job.status is Status.REJECTED
    assert not (workspace / "note.txt").exists()
    with pytest.raises(JobStateError):
        manager.approve(job.id)


def test_path_outside_workspace_is_refused_before_approval(manager):
    job = manager.submit("create ..\\evil.txt with x")
    assert job.status is Status.FAILED
    assert "outside the allowed folders" in job.error


def test_unknown_prompt_fails_with_help(manager):
    job = manager.submit("make me a sandwich")
    assert job.status is Status.FAILED
    assert "create <file> with <text>" in job.error


def test_approval_expires(settings, workspace):
    clock = [datetime.now(timezone.utc)]
    mgr = JobManager(settings, now=lambda: clock[0])
    job = mgr.submit("create late.txt with x")
    clock[0] += timedelta(minutes=settings.approval.timeout_minutes + 1)
    mgr.approve(job.id)
    assert job.status is Status.EXPIRED
    assert not (workspace / "late.txt").exists()


def test_failure_midway_rolls_back_earlier_steps(settings, workspace):
    class TwoStepPlanner:
        def plan(self, prompt, context=None):
            return Plan("two steps", [
                Step(Action.WRITE, "first.txt", "kept?"),
                Step(Action.DELETE, "missing.txt"),
            ])

    mgr = JobManager(settings, planner=TwoStepPlanner())
    job = mgr.submit("go")
    mgr.approve(job.id)
    assert job.status is Status.FAILED
    assert not (workspace / "first.txt").exists()


def test_undo_with_nothing_changed_is_an_error(manager):
    job = manager.submit("list")
    with pytest.raises(JobStateError):
        manager.undo(job.id)
