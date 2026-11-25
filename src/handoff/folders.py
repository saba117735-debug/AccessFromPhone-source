"""What the Admin sees and manages: every phone's kept folders (who owns each, who it is shared with), and a read-only list of who asked what.

Used by the Admin phone and by the PC page, so both show the same thing. Chats are not here: the Admin sees that a request was made, by whom and when, not what was said inside.
"""
from __future__ import annotations

from .accounts import AccountStore
from .jobs import JobManager

NOBODY = "From before phones had their own folders"


def _names(accounts: AccountStore | None) -> dict[str, str]:
    return {d["id"]: d["name"] for d in accounts.devices()} if accounts is not None else {}


def folders_overview(manager: JobManager, accounts: AccountStore | None) -> dict:
    """Every kept folder with its owner and the phones it is shared with, plus the phones it could be shared with."""
    names = _names(accounts)
    store = getattr(getattr(manager, "access", None), "store", None)
    phones = [{"id": d["id"], "name": d["name"], "admin": bool(store is not None and store.role(d["id"]) == "admin")} for d in (accounts.devices() if accounts is not None else []) if not d.get("blocked")]
    folders = []
    for e in manager.folder_entries():
        owner = e["owner"]
        folders.append({
            "path": e["path"], "owner": owner, "owner_name": names.get(owner, NOBODY if owner == "" else "A removed phone"), "change": e["change"],
            "shared": [{"id": k, "name": names.get(k, "A removed phone"), "change": bool(v)} for k, v in sorted(e["shared"].items(), key=lambda kv: names.get(kv[0], ""))],
        })
    return {"folders": folders, "phones": phones}


def parse_targets(raw: list, accounts: AccountStore | None, owner: str) -> dict[str, bool]:
    """The phones chosen in the share box, checked: each must be a paired phone, and not the owner."""
    targets: dict[str, bool] = {}
    for item in raw:
        device_id = item["id"] if isinstance(item, dict) else item.id
        change = bool(item["change"] if isinstance(item, dict) else item.change)
        if device_id == owner:
            continue
        if accounts is not None and accounts.get(device_id) is None:
            raise ValueError("That phone is not paired any more.")
        targets[device_id] = change
    return targets


def requests_overview(manager: JobManager, accounts: AccountStore | None, limit: int = 150) -> list[dict]:
    """Who asked what and when, newest first. Only the ask itself, its state and the time: no replies, steps or files (those stay in the phone's own chat)."""
    names = _names(accounts)
    out = []
    for job in manager.list()[:limit]:
        out.append({"id": job.id, "phone": names.get(job.owner, "Unknown phone" if job.owner else "This PC"), "owner": job.owner, "prompt": job.prompt[:200],
                    "status": job.status.value, "created_at": job.created_at.isoformat()})
    return out
