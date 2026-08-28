#!/usr/bin/env python3
"""Decide whether one CandidateFact reads as a statement or as a list, and segment it.

Span matching wants to tolerate word order *inside one statement*.  A resume-derived
fact is often not a statement at all: a skills-grid cell, a degree line, or a job
header is a row of unrelated items, and letting a pattern roam across it re-opens the
stitching hole that contiguity was there to close.

The discriminator is a finite verb, not the fact's `type`.  Type is where the fact came
from, which only correlates with structure by accident: a header can be prose
("Senior Analyst leading pharmaceutical market research"), and an experience claim can
be a fragment ("Python, SQL, focus groups").  Verb presence is the causal signal.

Uncertain input resolves to LIST.  Reading a list as prose invents evidence; reading
prose as a list only misses some, and this project would rather miss than stitch.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from evidence_matcher import tokens as _tokens  # noqa: E402


STRUCTURE_RULE_VERSION = "1.0.0"
STRUCTURES = ("PROSE", "LIST", "ATOMIC")

# Controlled stems. A token counts as a verb when it is the stem or the stem plus one
# of the accepted endings, so the table stays small and auditable.
VERB_STEMS = frozenset({
    "analys", "analyz", "apply", "appli", "assess", "build", "clean", "collaborat",
    "communicat", "conduct", "contribut", "creat", "deliver", "design", "develop",
    "enhanc", "establish", "evaluat", "extract", "foster", "generat", "harmoniz",
    "identify", "identifi", "implement", "improv", "increas", "integrat", "maintain",
    "manage", "maximiz", "optimiz", "partner", "perform", "process", "produc",
    "provid", "publish", "quantify", "quantifi", "reduc", "standardiz", "support",
    "synthes", "translat",
})
VERB_ENDINGS = ("", "d", "e", "ed", "es", "ied", "ing", "s")
IRREGULAR_VERBS = frozenset({
    "began", "brought", "built", "chose", "drew", "drove", "found", "gave", "grew",
    "held", "kept", "led", "made", "met", "ran", "sought", "spoke", "taught", "took",
    "won", "wrote",
})
# Light verbs carry the statement in Chinese; the tokenizer does not segment CJK, so
# these are matched as substrings rather than tokens.
CHINESE_VERBS = ("负责", "主导", "建立", "开发", "设计", "分析", "提升", "交付", "完成", "搭建")
# "Present" in a resume is the end of a date range, not the verb.
DATE_WORDS = frozenset({"present", "current", "ongoing"})

SEPARATORS = re.compile(r"[,;/|、，；]")
CLAUSE_BOUNDARY = re.compile(r"[.;!?,。；！？，、\n]")
CONNECTIVES = frozenset({"and", "or", "和", "与", "及", "或"})
# Fixed phrases whose internal "and" is not a boundary.
PROTECTED_PHRASES = (
    "research and development", "health and mental hygiene", "policies and procedures",
    "profit and loss", "terms and conditions",
)
SHORT_FACT_TOKENS = 6
LIST_SEGMENT_TOKENS = 3.0


def _verb_hits(text: str) -> list[tuple[int, str]]:
    hits = []
    for index, token in enumerate(_tokens(text)):
        if token in DATE_WORDS:
            continue
        if token in IRREGULAR_VERBS:
            hits.append((index, token))
            continue
        for stem in VERB_STEMS:
            if token.startswith(stem) and token[len(stem):] in VERB_ENDINGS:
                hits.append((index, token))
                break
    lowered = str(text)
    hits.extend((-1, verb) for verb in CHINESE_VERBS if verb in lowered)
    return hits


def classify(text: str) -> dict[str, Any]:
    """Return the lexical structure of one fact surface. Deterministic, no model."""
    value = str(text or "")
    token_list = _tokens(value)
    verbs = _verb_hits(value)
    # A title that opens with a gerund and carries no other verb is a heading, not a
    # statement: "Evaluating Mutation Status in ..." names a project.
    if len(verbs) == 1 and verbs[0][0] == 0 and verbs[0][1].endswith("ing"):
        return {"structure": "LIST", "reason": f"gerund_heading:{verbs[0][1]}",
                "rule_version": STRUCTURE_RULE_VERSION}
    if verbs:
        return {"structure": "PROSE", "reason": f"finite_verb:{verbs[0][1]}",
                "rule_version": STRUCTURE_RULE_VERSION}
    separators = len(SEPARATORS.findall(value))
    if separators and len(token_list) / (separators + 1) < LIST_SEGMENT_TOKENS:
        return {"structure": "LIST", "reason": "separator_density",
                "rule_version": STRUCTURE_RULE_VERSION}
    if len(token_list) < SHORT_FACT_TOKENS:
        return {"structure": "ATOMIC", "reason": "short_surface",
                "rule_version": STRUCTURE_RULE_VERSION}
    return {"structure": "LIST", "reason": "no_finite_verb",
            "rule_version": STRUCTURE_RULE_VERSION}


def _protect(value: str) -> str:
    for index, phrase in enumerate(PROTECTED_PHRASES):
        value = value.replace(phrase, f"\x00{index}\x00")
    return value


def segments(text: str, structure: str) -> list[list[str]]:
    """Split one surface into the units a pattern may match inside.

    PROSE  -> clause spans; a pattern's tokens may appear in any order inside one span.
    LIST   -> separator-delimited items; a pattern's tokens must stay contiguous.
    ATOMIC -> the whole surface as one unit.
    """
    if structure not in STRUCTURES:
        raise ValueError("unknown fact structure")
    value = _protect(str(text or "").casefold())
    if structure == "ATOMIC":
        return [_tokens(value)] if _tokens(value) else []
    if structure == "LIST":
        return [_tokens(part) for part in SEPARATORS.split(value) if _tokens(part)]
    out: list[list[str]] = []
    for part in CLAUSE_BOUNDARY.split(value):
        current: list[str] = []
        for token in _tokens(part):
            if token in CONNECTIVES:
                if current:
                    out.append(current)
                current = []
            else:
                current.append(token)
        if current:
            out.append(current)
    return out


def describe(text: str) -> dict[str, Any]:
    """Classification plus its segmentation, cached together on the EvidenceUnit."""
    result = classify(text)
    return {**result, "segments": segments(text, result["structure"])}
