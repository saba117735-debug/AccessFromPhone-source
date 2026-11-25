import threading

import pytest

from handoff.config import Action
from handoff.jobs import JobManager, Status
from handoff.planner import Plan, PlanError, Step
from handoff.policy import PathDenied


@pytest.fixture
def workspace(settings):
    return settings.agent.allowed_folders[0]


class Fixed:
    is_slow = False

    def __init__(self, plan=None, error=None):
        self._plan, self._error = plan, error

    def plan(self, prompt, context=None):
        if self._error:
            raise self._error
        return self._plan


def test_a_plain_reply_finishes_the_job_with_nothing_to_undo(settings):
    mgr = JobManager(settings, Fixed(Plan("", [], reply="Hello!")))
    job = mgr.submit("hi")
    assert job.status is Status.DONE and job.outputs == ["Hello!"] and job.changed == []


def test_slow_planner_runs_in_the_background(settings, workspace):
    class Slow:
        is_slow = True

        def __init__(self):
            self.gate = threading.Event()

        def plan(self, prompt, context=None):
            self.gate.wait(5)
            return Plan("write it", [Step(Action.WRITE, "x.txt", "1")])

    slow = Slow()
    mgr = JobManager(settings, slow)
    job = mgr.submit("go")
    assert job.status is Status.PLANNING  # the request returned before the model finished
    slow.gate.set()
    mgr.join_background()
    assert job.status is Status.AWAITING and not (workspace / "x.txt").exists()
    mgr.approve(job.id)
    assert (workspace / "x.txt").read_text(encoding="utf-8") == "1"


def test_planner_error_fails_the_job(settings):
    job = JobManager(settings, Fixed(error=PlanError("Cannot reach Ollama"))).submit("hi")
    assert job.status is Status.FAILED and "Cannot reach Ollama" in job.error


def test_unexpected_planner_crash_never_leaves_a_job_stuck(settings):
    job = JobManager(settings, Fixed(error=RuntimeError("boom"))).submit("hi")
    assert job.status is Status.FAILED and "RuntimeError: boom" in job.error


def test_a_proposed_path_outside_the_workspace_is_refused(settings):
    plan = Plan("sneaky", [Step(Action.WRITE, "..\\evil.txt", "x")])
    job = JobManager(settings, Fixed(plan)).submit("go")
    assert job.status is Status.FAILED and "outside the allowed folders" in job.error


def test_the_planner_can_only_read_inside_the_workspace(settings, project):
    settings.agent.ask_for_access = False  # strict mode
    (project / "secret.txt").write_text("nope", encoding="utf-8")
    seen = {}

    class Snoop:
        is_slow = False

        def plan(self, prompt, context=None):
            with pytest.raises(PathDenied):
                context.read_file("..\\secret.txt")
            with pytest.raises(PathDenied):
                context.list_dir("..")
            seen["checked"] = True
            return Plan("", [], reply="blocked")

    job = JobManager(settings, Snoop()).submit("snoop")
    assert seen == {"checked": True} and job.outputs == ["blocked"]


def test_the_planner_can_read_workspace_files(settings, workspace):
    (workspace / "a.txt").write_text("hello", encoding="utf-8")

    class Reader:
        is_slow = False

        def plan(self, prompt, context=None):
            return Plan("", [], reply=context.read_file("a.txt") + "|" + ",".join(context.list_dir(".")))

    assert JobManager(settings, Reader()).submit("x").outputs == ["hello|a.txt"]
