"""Work out which file someone meant when the name they typed is a little off.

People type "actionClient.ts" for actionsClient.ts, "askcore" for AskCore.tsx, "AskCore.ts" for AskCore.tsx.
A match this clear is used (and the reply says so). Several close ones are offered instead. Nothing close means
the file really is not there.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

AUTO_AT = 0.9    # this close, and clearly ahead of the rest: use it
MAYBE_AT = 0.72  # this close: offer it
CLEAR_LEAD = 0.08


@dataclass(frozen=True)
class Match:
    auto: str | None = None
    maybe: list[str] = field(default_factory=list)


def _stem(name: str) -> str:
    base, dot, _ = name.rpartition(".")
    return base if dot and base else name


def best_match(wanted: str, names: list[str]) -> Match:
    """Compare `wanted` with the real `names` in a folder."""
    w = wanted.lower()
    for name in names:
        if name.lower() == w:
            return Match(auto=name)  # only the capital letters differ
    scored: list[tuple[float, str]] = []
    for name in names:
        low = name.lower()
        score = difflib.SequenceMatcher(None, w, low).ratio()
        if _stem(low) == _stem(w):
            score = max(score, 0.95)  # same name, another (or no) extension
        elif len(_stem(w)) >= 4 and _stem(w) in low:
            score = max(score, 0.8)  # "askcore" inside "askcore.test.tsx"
        if score >= MAYBE_AT:
            scored.append((score, name))
    scored.sort(key=lambda s: (-s[0], s[1]))
    if not scored:
        return Match()
    top = scored[0][0]
    lead = top - scored[1][0] if len(scored) > 1 else 1.0
    if top >= AUTO_AT and lead >= CLEAR_LEAD:
        return Match(auto=scored[0][1])
    return Match(maybe=[name for _, name in scored[:4]])
