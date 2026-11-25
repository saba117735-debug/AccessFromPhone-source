"""A phone that pairs again: it replaces its own entry when it still has its token, and is told apart from others when it does not."""
import pytest

from handoff.accounts import AccountError, AccountStore
from handoff.db import Database


@pytest.fixture
def store(tmp_path):
    s = AccountStore(Database(tmp_path / "app.db"), pairing_minutes=2, max_devices=3)
    s.setup_owner("Afza", "correct horse")
    return s


def code(store):
    return store.new_pairing_code()[0]


def test_a_phone_that_pairs_again_with_its_token_keeps_its_one_entry(store):
    first, old = store.pair(code(store), "Pixel", "1.1.1.1")
    store.rename(first.id, "Afza's Pixel")
    again, new = store.pair(code(store), "Pixel", "1.1.1.2", previous=old)
    assert again.id == first.id and again.name == "Afza's Pixel"  # same entry, the owner's name kept
    assert len(store.devices()) == 1
    assert new != old and store.authenticate(old) is None and store.authenticate(new).id == first.id  # the old token is dead


def test_a_phone_that_lost_its_token_is_a_new_entry_with_a_distinct_name(store):
    store.pair(code(store), "Phone")
    second, _ = store.pair(code(store), "Phone")
    third, _ = store.pair(code(store), "Phone")
    assert [d["name"] for d in sorted(store.devices(), key=lambda d: d["created"] + d["id"])] and {second.name, third.name} == {"Phone (2)", "Phone (3)"}
    assert len({d["name"] for d in store.devices()}) == 3


def test_a_wrong_or_foreign_previous_token_never_takes_over_someone_elses_entry(store):
    mine, _ = store.pair(code(store), "Mine")
    _, theirs = store.pair(code(store), "Theirs")
    store.pair(code(store), "Intruder", previous="not-a-real-token")
    assert len(store.devices()) == 3 and store.get(mine.id).name == "Mine"
    assert store.authenticate(theirs) is not None  # untouched


def test_a_blocked_phone_is_not_let_back_in_by_pairing_again(store):
    device, token = store.pair(code(store), "Phone")
    store.set_blocked(device.id, True)
    new, new_token = store.pair(code(store), "Phone", previous=token)
    assert new.id != device.id and not new.blocked
    assert store.get(device.id).blocked and store.authenticate(token) is None  # the blocked entry stays, still blocked


def test_pairing_again_does_not_count_against_the_limit_but_a_new_phone_does(store):
    _, token = store.pair(code(store), "A")
    store.pair(code(store), "B")
    store.pair(code(store), "C")
    store.pair(code(store), "A", previous=token)  # full, yet this is a replacement
    with pytest.raises(AccountError, match="already has 3"):
        store.pair(code(store), "D")


def test_a_code_is_still_used_up_when_the_phone_pairs_again(store):
    _, token = store.pair(code(store), "A")
    c = code(store)
    store.pair(c, "A", previous=token)
    with pytest.raises(AccountError):
        store.pair(c, "B")


def test_phones_not_seen_for_a_long_time_are_forgotten_only_when_asked(tmp_path):
    for days, expected in ((0, 2), (30, 1)):
        s = AccountStore(Database(tmp_path / f"d{days}.db"), max_devices=5, forget_after_days=days)
        s.setup_owner("Afza", "correct horse")
        old, _ = s.pair(s.new_pairing_code()[0], "Old")
        s.pair(s.new_pairing_code()[0], "Recent")
        s._db.run("UPDATE devices SET last_seen='2020-01-01T00:00:00+00:00' WHERE id=?", (old.id,))
        assert len(s.devices()) == expected
        if days:
            assert s.get(old.id) is None
