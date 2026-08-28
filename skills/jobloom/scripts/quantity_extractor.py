"""Conservative extraction of work-result quantities from CandidateFact text."""

from __future__ import annotations

import re
from typing import Any


NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?\s*(?:%|\+|k|m|million|billion)?", re.I)
DATE_CONTEXT = re.compile(r"\b(?:19|20)\d{2}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}\b", re.I)
GPA_CONTEXT = re.compile(r"\b(?:gpa|grade point average)\s*[:=]?\s*\d", re.I)
COURSE_ENUM = re.compile(r"\b(?:course|regression|calculus|statistics|data science)\s+\d\s*(?:&|and|/)\s*\d\b", re.I)
RESULT_TERMS = re.compile(
    r"\b(?:increase|decrease|reduce|improve|grow|save|deliver|lead|manage|analy[sz]|present|"
    r"publish|launch|process|support|audience|team|budget|trial|product|site|record|patient)s?\w*\b|"
    r"提升|降低|增长|节省|交付|主导|管理|发布|受众|团队|患者|记录"
    , re.I,
)


def extract_quantities(value: str) -> list[dict[str, Any]]:
    text = str(value)
    if GPA_CONTEXT.search(text) or COURSE_ENUM.search(text):
        return []
    results = []
    for match in NUMBER.finditer(text):
        raw = match.group().strip()
        if re.fullmatch(r"(?:19|20)\d{2}", raw) or DATE_CONTEXT.fullmatch(raw):
            continue
        start, end = max(0, match.start() - 60), min(len(text), match.end() + 60)
        context = text[start:end]
        if DATE_CONTEXT.search(context) and not RESULT_TERMS.search(context):
            continue
        if not RESULT_TERMS.search(context):
            continue
        results.append({"value": raw, "start": match.start(), "end": match.end()})
    return results


def is_quantified(value: str) -> bool:
    return bool(extract_quantities(value))
