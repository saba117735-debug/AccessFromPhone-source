"""Naming a chat: what to ask the PC's own AI model, and how to make its answer safe to show.

Only the LOCAL model is ever asked. A hosted model would be sent the first request just to name the chat, including requests that are
otherwise handled without any model (opening a folder, reading a file), so those never leave the PC. The phone makes its own name from
the words (web/titles.js) and uses this one only when it arrives.
"""
from __future__ import annotations

import re

TITLE_SYSTEM = (
    "You name chats. The user's message is the first request in a chat with an assistant that works on their PC (files, folders, apps, power). "
    "Reply with a short title for the chat: 2 to 6 words, in the same language as the request, no quotes, no full stop, no label, nothing else. "
    "Say what the request is about, not how it is phrased. "
    "Examples: 'i want to open the output folder please open it' -> Open output folder. 'lock my pc' -> Lock PC. "
    "'what is in notes.txt' -> Contents of notes.txt. 'find all .bak files in documents' -> Find backup files."
)

_LABEL = re.compile(r"^\s*(?:title|name|chat title)\s*[:\-]\s*", re.I)
_REFUSAL = re.compile(r"^(?:i'm sorry|i am sorry|sorry|as an ai|i cannot|i can't|i can not)", re.I)
MAX_WORDS, MAX_CHARS = 9, 60


def clean_title(raw: str | None) -> str | None:
    """The model's answer as a title: one line, no label or quotes, a sensible length. None when it is not a usable name."""
    lines = [line for line in str(raw or "").splitlines() if line.strip()]
    if not lines:
        return None
    text = _LABEL.sub("", lines[0])
    text = text.strip(" \t\"'`*#").rstrip(" .:;-\"'`*#")
    text = " ".join(text.split())
    if not text or _REFUSAL.match(text):
        return None
    words = text.split(" ")
    if len(words) > MAX_WORDS:
        text = " ".join(words[:MAX_WORDS])
    if len(text) > MAX_CHARS:
        return None
    return text[0].upper() + text[1:]
