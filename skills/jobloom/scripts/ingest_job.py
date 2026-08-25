#!/usr/bin/env python3
"""Build a reviewable JobCard draft from a job URL, HTML, JSON, or plain-text JD."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from datetime import date
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin


class JobPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._json_depth = 0
        self._json_parts: list[str] = []
        self.json_ld: list[Any] = []
        self.text_parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type", "").casefold() == "application/ld+json":
            self._json_depth = 1
            self._json_parts = []
        elif tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._json_depth:
            try:
                self.json_ld.append(json.loads("".join(self._json_parts)))
            except json.JSONDecodeError:
                pass
            self._json_depth = 0
            self._json_parts = []
        elif tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._json_depth:
            self._json_parts.append(data)
        elif not self._skip_depth and data.strip():
            self.text_parts.append(data.strip())


def find_job_posting(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        kind = value.get("@type")
        if kind == "JobPosting" or isinstance(kind, list) and "JobPosting" in kind:
            return value
        for child in value.values():
            result = find_job_posting(child)
            if result:
                return result
    elif isinstance(value, list):
        for child in value:
            result = find_job_posting(child)
            if result:
                return result
    return None


def clean_html(value: str) -> str:
    parser = JobPageParser()
    parser.feed(value)
    return re.sub(r"\s+", " ", unescape(" ".join(parser.text_parts))).strip()


def nested_text(value: Any, *keys: str) -> str | None:
    current = value
    for key in keys:
        if isinstance(current, list):
            current = current[0] if current else None
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, list):
        current = current[0] if current else None
    if isinstance(current, dict):
        current = current.get("name") or current.get("value")
    return str(current).strip() if current not in (None, "") else None


def normalize_country(value: str | None) -> str:
    if not value:
        return "unknown"
    aliases = {
        "us": "US", "usa": "US", "united states": "US", "united states of america": "US",
        "ca": "CA", "canada": "CA",
        "gb": "GB", "uk": "GB", "united kingdom": "GB",
    }
    return aliases.get(value.strip().casefold(), value.strip().upper())


def normalize_employment(value: Any) -> str:
    text = " ".join(value) if isinstance(value, list) else str(value or "")
    normalized = text.casefold().replace("-", "_").replace(" ", "_")
    if "full" in normalized:
        return "full_time"
    if "part" in normalized:
        return "part_time"
    if "contract" in normalized or "temporary" in normalized:
        return "contract"
    return "unknown"


def normalize_salary(posting: dict[str, Any]) -> dict[str, Any] | None:
    salary = posting.get("baseSalary")
    if not isinstance(salary, dict):
        return None
    currency = salary.get("currency") or posting.get("salaryCurrency")
    value = salary.get("value", salary)
    if not isinstance(value, dict):
        return None
    minimum = value.get("minValue", value.get("value"))
    maximum = value.get("maxValue", value.get("value"))
    return {"currency": currency, "min": minimum, "max": maximum, "unit": value.get("unitText", "YEAR")}


def card_from_posting(posting: dict[str, Any], source_url: str, fallback_text: str) -> dict[str, Any]:
    description = clean_html(str(posting.get("description", ""))) or fallback_text
    employer = nested_text(posting, "hiringOrganization", "name") or "unknown"
    title = str(posting.get("title") or "unknown")
    country = normalize_country(nested_text(posting, "jobLocation", "address", "addressCountry"))
    locality = nested_text(posting, "jobLocation", "address", "addressLocality")
    region = nested_text(posting, "jobLocation", "address", "addressRegion")
    location = ", ".join(item for item in (locality, region) if item) or "unknown"
    remote = str(posting.get("jobLocationType", "")).casefold() == "telecommute"
    valid_through = posting.get("validThrough")
    status = "open"
    if valid_through:
        try:
            status = "closed" if date.fromisoformat(str(valid_through)[:10]) < date.today() else "open"
        except ValueError:
            status = "unknown"
    skills = posting.get("skills", [])
    if isinstance(skills, str):
        skills = [item.strip() for item in re.split(r"[,;]", skills) if item.strip()]
    job_id_seed = f"{employer}|{title}|{source_url}".encode("utf-8")
    return {
        "schema_version": "0.2.0",
        "job_id": f"job-{hashlib.sha256(job_id_seed).hexdigest()[:12]}",
        "canonical_url": urljoin(source_url, str(posting.get("url") or source_url)),
        "employer": employer,
        "title": title,
        "country": country,
        "location": location,
        "work_arrangement": "remote" if remote else "unknown",
        "employment_type": normalize_employment(posting.get("employmentType")),
        "salary": normalize_salary(posting),
        "status": status,
        "sponsorship": "unknown",
        "citizenship_required": None,
        "security_clearance_required": None,
        "required_certifications": [],
        "required_skills": skills,
        "preferred_skills": [],
        "already_applied": False,
        "high_value": False,
        "requirements_reviewed": False,
        "description": description,
        "description_sha256": hashlib.sha256(description.encode("utf-8")).hexdigest(),
        "extraction": {"strategy": "json_ld", "needs_user_review": True},
    }


def build_card(content: str, source_url: str, content_type: str = "html") -> dict[str, Any]:
    if content_type == "json":
        data = json.loads(content)
        posting = find_job_posting(data) or data
        return card_from_posting(posting, source_url, "")
    if content_type == "html":
        parser = JobPageParser()
        parser.feed(content)
        posting = next((find_job_posting(item) for item in parser.json_ld if find_job_posting(item)), None)
        fallback = re.sub(r"\s+", " ", unescape(" ".join(parser.text_parts))).strip()
        if posting:
            return card_from_posting(posting, source_url, fallback)
        content = fallback
    description = re.sub(r"\s+", " ", content).strip()
    seed = f"unknown|unknown|{source_url}|{description[:200]}".encode("utf-8")
    return {
        "schema_version": "0.2.0", "job_id": f"job-{hashlib.sha256(seed).hexdigest()[:12]}",
        "canonical_url": source_url, "employer": "unknown", "title": "unknown", "country": "unknown",
        "location": "unknown", "work_arrangement": "unknown", "employment_type": "unknown", "salary": None,
        "status": "unknown", "sponsorship": "unknown", "citizenship_required": None,
        "security_clearance_required": None, "required_certifications": [], "required_skills": [],
        "preferred_skills": [], "already_applied": False, "high_value": False, "requirements_reviewed": False,
        "description": description, "description_sha256": hashlib.sha256(description.encode("utf-8")).hexdigest(),
        "extraction": {"strategy": "plain_text", "needs_user_review": True},
    }


def fetch(url: str) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "Jobloom/0.2 (+local job evaluation)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        content_type = "json" if "json" in response.headers.get_content_type() else "html"
        return content, content_type


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url")
    source.add_argument("--file", type=Path)
    parser.add_argument("--source-url", default="local-file")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.url:
        content, content_type = fetch(args.url)
        source_url = args.url
    else:
        content = args.file.read_text(encoding="utf-8")
        content_type = "json" if args.file.suffix.casefold() == ".json" else "html" if args.file.suffix.casefold() in {".html", ".htm"} else "text"
        source_url = args.source_url
    card = build_card(content, source_url, content_type)
    args.output.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
