"""TitleSurface normalization and map-once cache behavior."""

from __future__ import annotations

import re
from typing import Any, Callable

LEVEL_END = re.compile(r"\s+(i|ii|iii|iv|v|1|2|3|4)$", re.I)
LEVEL_START = re.compile(r"^(sr|senior|jr|junior)\s+", re.I)
GUARDS = {"senior": {"care", "living", "housing", "services", "health", "citizen", "citizens"}}


def normalize(raw: str) -> tuple[str, str | None]:
    surface = re.sub(r"\s+", " ", str(raw).casefold()).strip(" ,.;:-")
    words = surface.split()
    protected = bool(len(words) > 1 and words[0] in GUARDS and words[1] in GUARDS[words[0]])
    level = None
    match = LEVEL_END.search(surface)
    if match:
        level, surface = match.group(1), surface[:match.start()]
    elif not protected and (match := LEVEL_START.match(surface)):
        level, surface = match.group(1), surface[match.end():]
    return surface, level


def observe(raw: str, cache: dict[str, dict[str, Any]], mapper: Callable[[str], list[dict[str, Any]]]) -> dict[str, Any]:
    base, level = normalize(raw)
    if base in cache:
        cache[base]["postings_seen"] = cache[base].get("postings_seen", 0) + 1
        return cache[base]
    mapped = {"raw": raw, "normalized": base, "level_token": level,
              "maps_to": mapper(raw), "postings_seen": 1}
    cache[base] = mapped
    return mapped
