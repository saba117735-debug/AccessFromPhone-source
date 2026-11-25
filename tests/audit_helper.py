"""Tests read the audit trail from the database, as the app does."""
from handoff.audit import AuditLog
from handoff.db import Database


def audit_text(settings) -> str:
    return AuditLog(Database(settings.storage.database)).as_text()
