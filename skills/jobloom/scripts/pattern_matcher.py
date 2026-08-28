"""Deterministic evidence-pattern matching for the capability ontology.

This module deliberately does not assign evidence grades.  It only answers
whether one controlled pattern matches one CandidateFact surface.  The caller
must cap the relation grade against the fact's existing evidence strength.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from evidence_matcher import EVIDENCE_ORDER, TOKEN_ALIASES, tokens as _tokens  # noqa: E402
import fact_structure  # noqa: E402


PATTERN_TYPES = {"token_run", "substring", "semantic_anchor"}
SUPPORTED_LANGUAGES = {"en", "zh"}
PATTERN_KEYS = {
    "token_run": ({"pattern_id", "type", "lang", "tokens"},
                  {"pattern_id", "type", "lang", "tokens", "inflect", "variants", "max_grade"}),
    "substring": ({"pattern_id", "type", "lang", "text"},
                  {"pattern_id", "type", "lang", "text", "variants", "max_grade"}),
    "semantic_anchor": ({
        "pattern_id", "type", "lang", "anchor", "max_grade", "requires_confirmation",
    }, {
        "pattern_id", "type", "lang", "anchor", "max_grade", "requires_confirmation",
    }),
}
COMMON_IRREGULAR_FORMS = {
    "analysis": {"analyses"},
    "datum": {"data"},
    "person": {"people"},
    "criterion": {"criteria"},
}


def _surfaces(fact: dict[str, Any] | str) -> list[str]:
    if isinstance(fact, str):
        return [fact]
    if not isinstance(fact, dict):
        raise TypeError("fact must be a string or mapping")
    values = [fact.get("value", ""), *(fact.get("keywords") or [])]
    return [str(value) for value in values if str(value)]


def _inflected_forms(base: str) -> set[str]:
    """Return a conservative set of surface forms for one controlled token."""
    base = TOKEN_ALIASES.get(base.casefold(), base.casefold())
    forms = {base, *COMMON_IRREGULAR_FORMS.get(base, set())}
    if len(base) < 3 or not base.isalpha():
        return forms

    if base.endswith("y") and len(base) > 3 and base[-2] not in "aeiou":
        forms.update({f"{base[:-1]}ies", f"{base[:-1]}ied"})
    else:
        forms.add(f"{base}s")
    if base.endswith(("s", "x", "z", "ch", "sh")):
        forms.add(f"{base}es")
    forms.add(f"{base}d" if base.endswith("e") else f"{base}ed")
    forms.add(f"{base[:-1]}ing" if base.endswith("e") and len(base) > 3 else f"{base}ing")
    return forms


MATCH_MODES = {"exact", "prefix", "lemma"}


def _token_spec(token: Any) -> tuple[str, str]:
    """A pattern token is either a bare string or {"token": ..., "match": ...}."""
    if isinstance(token, str):
        return token.strip().casefold(), "exact"
    if not isinstance(token, dict) or set(token) - {"token", "match"} or "token" not in token:
        raise ValueError("pattern token must be a string or {token, match}")
    surface = token["token"]
    if not isinstance(surface, str) or not surface.strip():
        raise ValueError("pattern token requires non-empty text")
    mode = token.get("match", "exact")
    if mode not in MATCH_MODES:
        raise ValueError("pattern token match mode is unsupported")
    return surface.strip().casefold(), mode


def _token_matches(expected: Any, observed: str, *, inflect: bool) -> bool:
    surface, mode = _token_spec(expected)
    surface = TOKEN_ALIASES.get(surface, surface)
    observed = TOKEN_ALIASES.get(observed.casefold(), observed.casefold())
    if mode == "prefix":
        # Controlled compound-root expansion: "data" reaches database/dataset, and the
        # widening is declared per token so it can never be switched on globally.
        return observed.startswith(surface)
    if mode == "lemma":
        return observed in _inflected_forms(surface)
    return observed == surface or (inflect and observed in _inflected_forms(surface))


def _variant_token_runs(pattern: dict[str, Any]) -> list[list[Any]]:
    runs: list[list[Any]] = [list(pattern["tokens"])]
    for variant in pattern.get("variants", []):
        if isinstance(variant, str):
            run = _tokens(variant)
        elif isinstance(variant, list):
            run = list(variant)
        else:
            raise ValueError("token_run variants must be strings or token lists")
        if not run:
            raise ValueError("token_run variants cannot be empty")
        runs.append(run)
    return runs


def _contiguous(segment: list[str], wanted: list[Any], *, inflect: bool) -> bool:
    width = len(wanted)
    return any(
        all(_token_matches(expected, observed, inflect=inflect)
            for expected, observed in zip(wanted, segment[start:start + width]))
        for start in range(len(segment) - width + 1)
    )


def _co_occurs(segment: list[str], wanted: list[Any], *, inflect: bool) -> bool:
    return all(any(_token_matches(expected, observed, inflect=inflect) for observed in segment)
               for expected in wanted)


def _contains_token_run(surface: str, wanted: list[Any], *, inflect: bool) -> bool:
    """Order-free inside one prose clause; contiguous everywhere else.

    A statement may reorder its own words, so requiring adjacency inside prose loses
    real evidence.  A list row is a set of unrelated cells, so adjacency is the only
    thing stopping a pattern from stitching two of them together.  The structure is
    decided by `fact_structure`, never by the fact's `type`.
    """
    described = fact_structure.describe(surface)
    order_free = described["structure"] == "PROSE"
    checker = _co_occurs if order_free else _contiguous
    return any(checker(segment, wanted, inflect=inflect) for segment in described["segments"])


def validate_pattern(pattern: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(pattern, dict):
        raise ValueError("evidence pattern must be an object")
    pattern_type = pattern.get("type")
    if pattern_type not in PATTERN_TYPES:
        raise ValueError("evidence pattern has an unsupported type")
    required, allowed = PATTERN_KEYS[pattern_type]
    if not required <= set(pattern) or not set(pattern) <= allowed:
        raise ValueError("evidence pattern has missing or unknown fields")
    pattern_id = pattern.get("pattern_id")
    if not isinstance(pattern_id, str) or not pattern_id.strip():
        raise ValueError("evidence pattern requires pattern_id")
    lang = pattern.get("lang")
    if lang not in SUPPORTED_LANGUAGES:
        raise ValueError("evidence pattern requires a supported lang")

    value = dict(pattern)
    value["pattern_id"] = pattern_id.strip()
    max_grade = pattern.get("max_grade")
    if max_grade is not None and max_grade not in EVIDENCE_ORDER:
        raise ValueError("evidence pattern max_grade is invalid")
    if pattern_type == "token_run":
        if lang == "zh":
            raise ValueError("Chinese evidence patterns must use substring matching")
        tokens = pattern.get("tokens")
        if not isinstance(tokens, list) or not tokens:
            raise ValueError("token_run pattern requires non-empty tokens")
        if not isinstance(pattern.get("inflect", False), bool):
            raise ValueError("token_run inflect must be Boolean")
        normalized = []
        for token in tokens:
            surface, mode = _token_spec(token)
            normalized.append(surface if mode == "exact" else {"token": surface, "match": mode})
        value["tokens"] = normalized
        value["inflect"] = pattern.get("inflect", False)
        _variant_token_runs(value)
    elif pattern_type == "substring":
        text = pattern.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("substring pattern requires text")
        variants = pattern.get("variants", [])
        if (not isinstance(variants, list)
                or any(not isinstance(item, str) or not item.strip() for item in variants)):
            raise ValueError("substring variants must be non-empty strings")
        value["text"] = text.strip().casefold()
        value["variants"] = [item.strip().casefold() for item in variants]
    else:
        anchor = pattern.get("anchor")
        if not isinstance(anchor, str) or not anchor.strip():
            raise ValueError("semantic_anchor pattern requires anchor")
        max_grade = pattern.get("max_grade")
        if (max_grade not in EVIDENCE_ORDER
                or EVIDENCE_ORDER[max_grade] > EVIDENCE_ORDER["transferable"]):
            raise ValueError("semantic_anchor max_grade cannot exceed transferable")
        if pattern.get("requires_confirmation") is not True:
            raise ValueError("semantic_anchor must require confirmation")
        value["anchor"] = anchor.strip()
    return value


def pattern_term(pattern: dict[str, Any]) -> str:
    """Human-readable term for one pattern, safe now that tokens may carry match modes."""
    pattern = validate_pattern(pattern)
    if pattern["type"] == "token_run":
        return " ".join(_token_spec(token)[0] for token in pattern["tokens"])
    if pattern["type"] == "substring":
        return pattern["text"]
    return pattern["anchor"]


def match(
    pattern: dict[str, Any],
    fact: dict[str, Any] | str,
    *,
    semantic_result: dict[str, Any] | bool | None = None,
    verified: bool = False,
) -> bool:
    """Match one validated pattern without assigning or upgrading a grade."""
    pattern = validate_pattern(pattern)
    pattern_type = pattern["type"]
    if pattern_type == "token_run":
        return any(
            _contains_token_run(surface, run, inflect=pattern["inflect"])
            for surface in _surfaces(fact)
            for run in _variant_token_runs(pattern)
        )
    if pattern_type == "substring":
        needles = [pattern["text"], *pattern.get("variants", [])]
        return any(needle in surface.casefold()
                   for surface in _surfaces(fact) for needle in needles)

    if semantic_result is None:
        return False
    if isinstance(semantic_result, bool):
        hit, confirmed = semantic_result, False
    elif isinstance(semantic_result, dict):
        hit = semantic_result.get("hit") is True
        confirmed = semantic_result.get("confirmed_by_user") is True
    else:
        raise TypeError("semantic_result must be Boolean or mapping")
    return hit and (not verified or confirmed)


def unmatched_pattern_ids(
    patterns: Iterable[dict[str, Any]],
    golden_facts: dict[str, list[dict[str, Any] | str]],
) -> list[str]:
    """Return deterministic patterns that fail every declared golden fact."""
    missing = []
    for raw in patterns:
        pattern = validate_pattern(raw)
        pattern_id = pattern["pattern_id"]
        samples = golden_facts.get(pattern_id, [])
        if pattern["type"] == "semantic_anchor":
            if not samples:
                missing.append(pattern_id)
            continue
        if not samples or not any(match(pattern, sample) for sample in samples):
            missing.append(pattern_id)
    return sorted(missing)


def resolve_semantic_cached(pattern: dict[str, Any], fact_id: str, fact: dict[str, Any] | str,
                            model_version: str, cache: dict[tuple[str, str, str], bool],
                            resolver: Any) -> bool:
    """Call the semantic resolver once per pattern/fact/model tuple."""
    pattern = validate_pattern(pattern)
    if pattern["type"] != "semantic_anchor":
        raise ValueError("semantic cache accepts semantic_anchor patterns only")
    key = (pattern["pattern_id"], fact_id, model_version)
    if key not in cache:
        cache[key] = bool(resolver(pattern["anchor"], fact))
    return cache[key]
