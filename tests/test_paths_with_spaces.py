"""A folder name with spaces in it ("OneDrive - Work") is one path, not several words."""
import os

import pytest

from handoff.config import Action
from handoff.jobs import JobManager, Status
from handoff.planner import DemoPlanner, drive_paths, mentioned_paths, quick_for, strip_drive_paths

pytestmark = pytest.mark.skipif(os.name != "nt", reason="drive-letter paths")


@pytest.fixture
def spaced(tmp_path):
    folder = tmp_path / "OneDrive - Work" / "Desktop" / "Pictures" / "Screen shots"
    folder.mkdir(parents=True)
    (folder / "a.png").write_bytes(b"x")
    (folder / "notes v1.2.txt").write_text("hello", encoding="utf-8")
    return folder


def test_a_path_with_spaces_is_found_whole(spaced):
    assert drive_paths(f"get the count of total imgs in this path {spaced}") == [str(spaced)]
    assert drive_paths(f"count the images in {spaced}.") == [str(spaced)]      # a full stop at the end of a sentence is not part of it
    assert drive_paths(f"what is in {spaced}?") == [str(spaced)]
    assert drive_paths(str(spaced)) == [str(spaced)]
    assert drive_paths(str(spaced).replace("\\", "/")) == [str(spaced).replace("\\", "/")]


def test_words_after_the_path_are_not_swallowed(spaced):
    parent = spaced.parent
    assert drive_paths(f"list {parent} please") == [str(parent)]
    assert drive_paths(f"list {parent} and tell me how big it is") == [str(parent)]


def test_a_file_inside_a_folder_with_spaces(spaced):
    full = spaced / "notes v1.2.txt"
    assert drive_paths(f"open {full}") == [str(full)]
    assert drive_paths(f"open {full} for me") == [str(full)]


def test_two_paths_in_one_request_are_both_found(spaced, tmp_path):
    other = tmp_path / "plain"
    other.mkdir()
    assert drive_paths(f"compare {spaced} with {other}") == [str(spaced), str(other)]


def test_a_path_that_is_not_there_reads_as_it_always_did(tmp_path):
    missing = tmp_path / "no such folder" / "file.txt"
    assert drive_paths(f"read {missing}") == [str(tmp_path / "no")]  # up to the first space: there is nothing to say it goes on
    assert drive_paths("nothing to find here") == []


def test_quoted_paths_still_work(spaced):
    plan = quick_for(f'open "{spaced / "notes v1.2.txt"}"', None)
    assert plan and plan.steps[0].path == str(spaced / "notes v1.2.txt")


def test_folder_names_inside_a_path_are_not_mistaken_for_file_names(spaced):
    # "v1.2" in the folder name must not become a file to look for
    assert strip_drive_paths(f"read notes in {spaced}").split() == ["read", "notes", "in"]


def test_a_plain_request_for_a_file_in_a_spaced_folder_is_understood_without_a_model(spaced):
    plan = quick_for(f"open {spaced / 'notes v1.2.txt'}", None)
    assert plan is not None and plan.steps and plan.steps[0].path == str(spaced / "notes v1.2.txt")
    assert plan.steps[0].action is Action.READ


def test_reading_requests_ask_for_the_whole_folder_up_front(spaced):
    assert mentioned_paths(f"get the count of total imgs in this path {spaced}") == [str(spaced)]
    assert mentioned_paths(f"delete everything in {spaced}") == []  # changes are asked about later, as before


def test_the_folder_the_phone_is_asked_to_allow_is_the_whole_one(settings, spaced):
    manager = JobManager(settings, DemoPlanner())
    job = manager.submit(f"get the count of total imgs in this path {spaced}")
    manager.join_background()
    assert job.status is Status.NEEDS_ACCESS
    assert job.access == [str(spaced)]  # not ...\OneDrive - Work cut off at the first space
    assert job.error is None
