"""Opening apps and using Teams from the phone: the Start-menu lookup, the Teams driver (with a fake window), the wording that asks for it, and the approvals."""
import pytest

from handoff.appctl import AppController, AppEntry, AppError, Chat, TeamsDriver, find_app, format_messages, match_chats, parse_chat, start_apps
from handoff.config import Action
from handoff.jobs import JobManager, Status
from handoff.planner import DemoPlanner, quick_for

APPS = [AppEntry("Microsoft Teams", "MSTeams_8wekyb3d8bbwe!MSTeams"), AppEntry("Notepad", "np"), AppEntry("Notepad++", "npp"), AppEntry("Outlook", "o1"),
        AppEntry("Outlook (classic)", "o2"), AppEntry("Excel", "x"), AppEntry("Google Chrome", "c"), AppEntry("Calculator", "calc"),
        AppEntry("Uninstall Microsoft Teams", "u"), AppEntry("SAP Interactive Excel 3.0 Documentation", "doc")]


# -- the Start menu ------------------------------------------------------------------------------------

@pytest.mark.parametrize("said,found", [("teams", "Microsoft Teams"), ("Microsoft Teams", "Microsoft Teams"), ("outlook", "Outlook"), ("notepad", "Notepad"), ("chrome", "Google Chrome"),
                                        ("calc", "Calculator"), ("the Excel app", "Excel"), ("note", "Notepad")])
def test_an_app_is_found_by_the_name_a_person_says(said, found):
    assert find_app(said, APPS).name == found


def test_nothing_is_opened_that_is_not_there_and_uninstallers_are_never_chosen():
    assert find_app("zzzz", APPS) is None
    assert "Uninstall" not in find_app("teams", [AppEntry("Uninstall Microsoft Teams", "u")] + APPS).name
    assert find_app("teams", [AppEntry("Uninstall Microsoft Teams", "u")]) is None


def test_the_start_menu_is_read_once_and_remembered():
    import handoff.appctl as module

    module._apps_cache.update(at=0.0, apps=[])
    calls = []

    class Done:
        stdout = b'[{"Name":"Notepad","AppID":"np"},{"Name":"Caf\xc3\xa9","AppID":"cf"}]'

    def run(*a, **k):
        calls.append(1)
        return Done()

    first = start_apps(run=run)
    assert [a.name for a in first] == ["Notepad", "Caf\u00e9"] and start_apps(run=run) == first and len(calls) == 1
    module._apps_cache.update(at=0.0, apps=[])


def test_an_app_id_with_odd_characters_is_not_passed_on():
    from handoff.appctl import launch

    with pytest.raises(AppError):
        launch(AppEntry("Bad", 'x" & calc'), starter=lambda c: None)
    seen = []
    launch(AppEntry("Teams", "MSTeams_8wekyb3d8bbwe!MSTeams"), starter=seen.append)
    assert seen == [["explorer.exe", "shell:AppsFolder\\MSTeams_8wekyb3d8bbwe!MSTeams"]]


# -- chat names ----------------------------------------------------------------------------------------

def test_a_chat_name_is_cleaned_of_teams_extras():
    assert parse_chat("Chat T J, Jayalakshmi Away").name == "T J, Jayalakshmi"
    assert parse_chat("Chat Kumar M, Vinod Unread").unread and parse_chat("Chat Kumar M, Vinod Unread").name == "Kumar M, Vinod"
    group = parse_chat("Group chat P, Balamurugan, +5 Has pinned messages")
    assert group.group and group.name == "P, Balamurugan, +5"
    assert parse_chat("Chat Ulla, Afzal (You)").name == "Ulla, Afzal (You)"


CHATS = [parse_chat(r) for r in ["Chat Ulla, Afzal (You)", "Group chat PPM Lalit TJ Vinod Sai Theja", "Chat Kumar M, Vinod Unread", "Chat Baari, Syed Abdul", "Chat Kumar, Vinod Prasad", "Chat E N, Niharika Unread"]]


def test_a_first_name_finds_the_chat_and_one_to_one_beats_a_group():
    assert [c.name for c in match_chats("niharika", CHATS)] == ["E N, Niharika"]
    assert [c.name for c in match_chats("syed", CHATS)] == ["Baari, Syed Abdul"]
    names = [c.name for c in match_chats("vinod", CHATS)]
    assert "Kumar M, Vinod" in names and all("Group" not in n and "PPM" not in n for n in names)


def test_a_name_can_be_typed_either_way_round_and_is_exact_when_it_can_be():
    assert [c.name for c in match_chats("vinod kumar m", CHATS)] == ["Kumar M, Vinod"]
    assert [c.name for c in match_chats("Kumar M, Vinod", CHATS)] == ["Kumar M, Vinod"]


# -- the driver, with a pretend Teams window ------------------------------------------------------------

class FakeTeams:
    def __init__(self, chats=None, running=True, iconic=False, box_text="Type a message\n", front=True, title=None, echo=True):
        self.chats = chats or ["Chat Kumar M, Vinod Unread", "Chat E N, Niharika Unread", "Chat Baari, Syed Abdul", "Chat Ulla, Afzal (You)"]
        self.running, self.iconic, self.front, self.echo = running, iconic, front, echo
        self.box, self.title, self.typed, self.pressed, self.minimized, self.opened = box_text, title, [], 0, 0, None
        self.log, self.started = [], 0
        self.messages = {"Chat Kumar M, Vinod Unread": [{"author": "Kumar M, Vinod", "time": "3:41 PM", "text": "come to T floor"}],
                         "Chat E N, Niharika Unread": [{"author": "E N, Niharika", "time": "3:30 PM", "text": "hi, where are you"}, {"author": "", "time": "3:31 PM", "text": "?"}]}

    def call(self, args):
        cmd = args["cmd"]
        self.log.append(cmd)
        if not self.running:
            return {"ok": False, "code": "not_running", "error": "Teams is not open on this PC."}
        if cmd == "list":
            return {"ok": True, "iconic": self.iconic, "front": self.front, "chats": [{"raw": c} for c in self.chats]}
        if cmd == "read":
            self.opened = args["name"]
            name = parse_chat(args["name"]).name
            return {"ok": True, "iconic": self.iconic, "front": self.front, "selected": True, "title": self.title if self.title is not None else name,
                    "messages": self.messages.get(args["name"], [])[-args["limit"]:]}
        if cmd == "focus":
            return {"ok": True, "front": self.front, "text": self.box}
        if cmd == "readback":
            return {"ok": True, "text": self.box if not self.typed else (self.typed[-1] if self.echo else "something else")}
        if cmd == "messages":
            return {"ok": True, "title": "", "messages": [{"author": "You", "time": "now", "text": self.typed[-1]}] if self.pressed and self.typed else []}
        if cmd == "minimize":
            self.minimized += 1
            return {"ok": True}
        return {"ok": False, "error": "?"}

    def driver(self):
        def start():
            self.started += 1
            self.running = True
        return TeamsDriver(call=self.call, type_text=self.typed.append, press_enter=lambda: setattr(self, "pressed", self.pressed + 1), sleep=lambda s: None, starter=start)


def test_unread_chats_are_listed_with_what_was_said():
    fake = FakeTeams()
    lines = "\n".join(fake.driver().unread_report())
    assert "2 unread Teams chats" in lines and "Kumar M, Vinod" in lines and "come to T floor" in lines
    assert "E N, Niharika: hi, where are you" in lines and "?" in lines
    assert "Baari" not in lines and fake.pressed == 0 and fake.typed == []


def test_with_nothing_unread_it_says_so_and_lists_the_latest_chats():
    lines = "\n".join(FakeTeams(chats=["Chat Baari, Syed Abdul", "Chat Ulla, Afzal (You)"]).driver().unread_report())
    assert "no unread" in lines.lower() and "Baari, Syed Abdul" in lines


def test_one_persons_latest_messages_can_be_read():
    lines = "\n".join(FakeTeams().driver().chat_report("vinod"))
    assert "Latest messages with Kumar M, Vinod" in lines and "come to T floor" in lines and "reply" in lines.lower()


def test_an_unknown_or_ambiguous_name_stops_with_a_clear_message():
    with pytest.raises(AppError, match="couldn't find"):
        FakeTeams().driver().chat_report("zebra")
    fake = FakeTeams(chats=["Chat Kumar M, Vinod", "Chat Kumar, Vinod Prasad"])
    with pytest.raises(AppError, match="More than one chat matches 'vinod'"):
        fake.driver().reply("vinod", "coming")
    assert fake.typed == [] and fake.pressed == 0


def test_a_reply_is_typed_exactly_and_sent_with_enter():
    fake = FakeTeams()
    lines = fake.driver().reply("vinod", "  I'm   coming  ")
    assert fake.typed == ["I'm coming"] and fake.pressed == 1
    assert lines[0] == "Sent to Kumar M, Vinod in Teams:" and "I'm coming" in lines[1] and len(lines) == 2
    assert fake.opened.startswith("Chat Kumar M, Vinod")


@pytest.mark.parametrize("kwargs,message", [
    (dict(front=False), "front"),
    (dict(box_text="half a sentence I was typing"), "already has text"),
    (dict(title="Someone Else Entirely"), "different chat"),
])
def test_it_will_not_type_when_something_is_off(kwargs, message):
    fake = FakeTeams(**kwargs)
    with pytest.raises(AppError, match=message):
        fake.driver().reply("vinod", "hello")
    assert fake.typed == [] and fake.pressed == 0


def test_if_the_box_does_not_hold_what_was_typed_nothing_is_sent():
    fake = FakeTeams(echo=False)
    with pytest.raises(AppError, match="isn't what I typed"):
        fake.driver().reply("vinod", "hello")
    assert fake.pressed == 0


def test_an_empty_or_huge_message_is_refused():
    with pytest.raises(AppError):
        FakeTeams().driver().reply("vinod", "   ")
    with pytest.raises(AppError, match="too long"):
        FakeTeams().driver().reply("vinod", "x" * 2000)


def test_teams_that_is_closed_is_started_and_one_that_was_minimised_goes_back_down():
    fake = FakeTeams(running=False, iconic=True)
    fake.driver().unread_report()
    assert fake.started == 1 and fake.minimized == 1


def test_messages_from_the_same_sender_without_a_name_carry_the_last_one():
    lines = format_messages([{"author": "E N, Niharika Sent", "time": "", "text": "hi"}, {"author": "", "time": "", "text": "there"}], Chat("x", "E N, Niharika", False, True))
    assert lines == ["  E N, Niharika: hi", "  E N, Niharika: there"]


# -- the wording -----------------------------------------------------------------------------------------

class Ctx:
    def __init__(self, last=None, apps=APPS):
        self._last = last
        self.start_apps = (lambda: apps) if apps is not None else None

    def last_teams(self):
        return self._last

    def history(self):
        return []


def steps(text, last=None):
    plan = quick_for(text, Ctx(last))
    return None if plan is None else [(s.app_kind, s.pattern, s.content) for s in plan.steps if s.action is Action.APP]


@pytest.mark.parametrize("text,app", [("open teams", "Microsoft Teams"), ("Open the Excel app", "Excel"), ("please launch chrome", "Google Chrome"), ("open calc", "Calculator"), ("start notepad", "Notepad")])
def test_open_any_app_in_the_start_menu(text, app):
    assert steps(text) == [("open", app, None)]


@pytest.mark.parametrize("text", ["open notes.txt", "open zzzz", "open downloads", "open my pictures folder", "open C:\\work\\a.py"])
def test_files_folders_and_unknown_names_are_not_apps(text):
    assert steps(text) in (None, [])


def test_nothing_is_opened_when_apps_are_switched_off():
    assert quick_for("open teams", Ctx(apps=None)) is None or not quick_for("open teams", Ctx(apps=None)).steps or quick_for("open teams", Ctx(apps=None)).steps[0].action is not Action.APP


@pytest.mark.parametrize("text", ["show my unread teams messages", "check teams messages", "check the latest message in teams", "any new messages on teams?", "what are my teams chats"])
def test_ask_for_unread_teams_messages(text):
    assert steps(text) == [("teams_unread", None, None)]


@pytest.mark.parametrize("text,who", [("what did Vinod say on teams", "Vinod"), ("show messages from vinod on teams", "vinod"), ("read vinod's messages on teams", "vinod")])
def test_ask_for_one_persons_messages(text, who):
    assert steps(text) == [("teams_chat", who, None)]


@pytest.mark.parametrize("text,who,message", [
    ("reply to vinod: I'm coming", "vinod", "I'm coming"),
    ("reply to Vinod Kumar saying on my way", "Vinod Kumar", "on my way"),
    ("reply to vinod \"I'm coming\"", "vinod", "I'm coming"),
    ("reply to vinod im coming on teams", "vinod", "im coming"),
    ("send vinod on teams: coming in 5", "vinod", "coming in 5"),
    ("tell vinod on teams that I'm coming", "vinod", "I'm coming"),
    ("reply to vinod: please send the file", "vinod", "please send the file"),
    ("reply to vinod: I'm coming and send", "vinod", "I'm coming"),
])
def test_reply_to_someone_with_exact_words(text, who, message):
    assert steps(text) == [("teams_reply", who, message)]


@pytest.mark.parametrize("text", ["how do I install teams", "mute teams notifications", "send me the report", "reply to the email", "reply to the email: thanks"])
def test_other_requests_are_left_alone(text):
    assert not steps(text)


@pytest.mark.parametrize("text,message", [("reply: I'm coming", "I'm coming"), ("reply saying I'm coming", "I'm coming"), ("reply to him: ok", "ok"), ("im coming and send", "im coming"), ("I'm coming, send it", "I'm coming")])
def test_after_reading_a_chat_a_reply_needs_no_name(text, message):
    assert steps(text, last="Vinod") == [("teams_reply", "Vinod", message)]


def test_without_a_chat_in_view_those_are_not_replies_and_a_different_job_is_not_mistaken_for_one():
    assert not steps("im coming and send")
    assert not steps("summarize the file and send", last="Vinod")


# -- approvals and running ----------------------------------------------------------------------------------

class FakeApps(AppController):
    def __init__(self, teams=None):
        self.ran = []
        self.teams = teams
        self._apps = lambda: APPS
        self._starter = None

    def run(self, kind, target, text):
        self.ran.append((kind, target, text))
        return [f"did {kind} {target}"]


class Unlocker:
    def __init__(self, locked=False):
        self.info = {"running": True, "configured": True, "enabled": True, "attention": False, "locked": locked}

    def status(self):
        return self.info


def make(settings, locked=False):
    apps = FakeApps()
    return JobManager(settings, DemoPlanner(), unlocker=Unlocker(locked), apps=apps), apps


def submit(manager, text):
    job = manager.submit(text)
    manager.join_background()
    return manager.get(job.id)


def test_opening_an_app_asks_first_then_does_it(settings):
    manager, apps = make(settings)
    job = submit(manager, "open teams")
    assert job.status is Status.AWAITING and apps.ran == []
    step = job.to_dict()["steps"][0]
    assert step["action"] == "app" and step["app"] == "open" and "Microsoft Teams" in step["detail"]
    done = manager.approve(job.id)
    assert done.status is Status.DONE and apps.ran == [("open", "Microsoft Teams", None)] and "did open" in done.outputs[0]


def test_opening_an_app_can_be_set_to_not_ask(settings):
    settings.apps.approve_open = False
    manager, apps = make(settings)
    job = submit(manager, "open notepad")
    assert job.status is Status.DONE and apps.ran == [("open", "Notepad", None)]


def test_opening_apps_can_be_switched_off(settings):
    settings.apps.enabled = False
    manager, apps = make(settings)
    job = submit(manager, "open notepad")
    assert not any(s.action is Action.APP for s in job.steps) and apps.ran == []


def test_teams_always_asks_and_shows_exactly_what_will_be_sent(settings):
    settings.approval.require_for = []
    manager, apps = make(settings)
    job = submit(manager, "reply to vinod: I'm coming")
    assert job.status is Status.AWAITING and apps.ran == []
    step = job.to_dict()["steps"][0]
    assert "vinod" in step["detail"] and "I'm coming" in step["detail"] and "exactly" in step["detail"]
    assert "can't be unsent" in step["warning"]
    manager.approve(job.id)
    assert apps.ran == [("teams_reply", "vinod", "I'm coming")]


def test_a_locked_pc_gets_a_clear_answer_instead_of_typing(settings):
    manager, apps = make(settings, locked=True)
    job = submit(manager, "show my unread teams messages")
    done = manager.approve(job.id) if job.status is Status.AWAITING else job
    assert done.status is Status.FAILED and "unlock my PC" in (done.error or "") and apps.ran == []


def test_teams_can_be_switched_off(settings):
    settings.apps.teams = False
    manager, apps = make(settings)
    job = submit(manager, "show my unread teams messages")
    assert job.status is Status.FAILED or not any(s.action is Action.APP for s in job.steps)
    assert apps.ran == []


def test_a_reply_straight_after_reading_a_chat_goes_to_that_person(settings):
    manager, apps = make(settings)
    first = submit(manager, "what did Vinod say on teams")
    manager.approve(first.id)
    second = submit(manager, "reply: I'm coming")
    assert [(s.app_kind, s.pattern, s.content) for s in second.steps] == [("teams_reply", "Vinod", "I'm coming")]


def test_the_message_is_not_written_to_the_audit_log(settings):
    import json

    manager, apps = make(settings)
    job = submit(manager, "reply to vinod: my secret plan")
    manager.approve(job.id)
    events = manager._audit.read()
    assert any(e["event"] == "app" for e in events)
    assert "my secret plan" not in json.dumps([e for e in events if e["event"] in ("app", "app_failed")])


@pytest.mark.parametrize("who", ["me", "myself", "self", "afzal", "ulla afzal"])
def test_your_own_chat_can_be_named_in_several_ways(who):
    fake = FakeTeams()
    lines = fake.driver().reply(who, "testing")
    assert lines[0] == "Sent to Ulla, Afzal (You) in Teams:" and fake.typed == ["testing"]


@pytest.mark.parametrize("text", ["reply to myself: testing", "message myself on teams: testing", "send a message to myself on teams saying testing", "tell afzal on teams that testing", "reply to afzal testing on teams"])
def test_messaging_yourself_is_understood(text):
    assert steps(text) and steps(text)[0][0] == "teams_reply" and steps(text)[0][2] == "testing"
