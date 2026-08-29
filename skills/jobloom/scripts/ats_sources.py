#!/usr/bin/env python3
"""Pull postings from company ATS public job-board APIs into reviewable JobCards.

Every adapter here reads a first-party endpoint the ATS publishes for public,
unauthenticated consumption. No page is scraped, no browser session is reused, and no
credential is ever sent. A board is only pulled after it has been registered with a
recorded authorization basis, so the set of endpoints Jobloom touches is enumerable and
auditable from one file.

What this module does *not* do: decide relevance. A pull filter narrows what is fetched
on hard, checkable facts (status, country, arrangement, date). Which postings are worth
an application stays with the approved direction profile and `direction_core.route_job`,
which reads a reviewed JobCard and never the raw posting.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

SCRIPT_DIR = str(Path(__file__).resolve().parent)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import application_core  # noqa: E402
import ingest_job  # noqa: E402
import posting_sections  # noqa: E402

SCHEMA_VERSION = "0.1.0"
CARD_SCHEMA_VERSION = "0.3.0"
REGISTRY_FILENAME = "ats-sources.json"
USER_AGENT = "Jobloom/0.3 (+local job evaluation; public job board API)"
REQUEST_TIMEOUT = 30
MAX_POSTINGS = 2000
MAX_PAGES = 25
PAGE_SIZE = 100
MAX_DESCRIPTION_CHARS = 200_000
MAX_TITLE_CHARS = 300
MAX_COMPENSATION_ITEMS = 20
MAX_COMPENSATION_CHARS = 300
DETAIL_REQUEST_DELAY = 0.25
BOARD_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
# Authorization says who carries the compliance judgement for reading a source. It is a
# property of the source, never a claim about the data's quality, and it is never inferred:
# each adapter declares it and a card carries the declaration through to the archive.
#
# Tier 5 exists so that a source we know is not platform-permitted has an honest, isolated
# place to live instead of being quietly washed into Tier 0. Adding it does not relax
# Tier 0: Workday belongs in Tier 5 precisely because it cannot enter Tier 0.
PUBLIC_JOB_BOARD_API = "public_job_board_api"
SELF_ASSERTED = "self_asserted"
AUTHORIZATIONS: dict[str, dict[str, Any]] = {
    PUBLIC_JOB_BOARD_API: {
        "tier": 0,
        "platform_permitted": True,
        "requires_operator_justification": False,
        "description": "the platform publishes this endpoint for public, unauthenticated reading",
    },
    SELF_ASSERTED: {
        "tier": 5,
        "platform_permitted": False,
        "requires_operator_justification": True,
        "description": ("read through an endpoint the platform does not document for outside "
                        "consumption; the operator carries the compliance judgement, not the platform"),
    },
}
# What may be described to a user as a clean, platform-permitted source. Everything else has
# to be labelled as what it is wherever provenance is shown.
PLATFORM_PERMITTED = frozenset(name for name, spec in AUTHORIZATIONS.items()
                               if spec["platform_permitted"])
# Kept for callers that predate the tiers.
AUTHORIZATION_BASIS = PUBLIC_JOB_BOARD_API


class SourceError(RuntimeError):
    """A registered board could not be read as its ATS documents it."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# --- Normalization ---------------------------------------------------------
# Deliberately small and closed. An unrecognized country is "unknown", never the raw
# string uppercased: a card that claims "EUROPEAN UNION" as a country code would pass a
# country filter it was never checked against.
COUNTRY_ALIASES = {
    "us": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US", "united states": "US",
    "united states of america": "US", "america": "US",
    "ca": "CA", "canada": "CA",
    "gb": "GB", "uk": "GB", "u.k.": "GB", "united kingdom": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "northern ireland": "GB", "great britain": "GB",
    "ie": "IE", "ireland": "IE", "de": "DE", "germany": "DE", "deutschland": "DE",
    "fr": "FR", "france": "FR", "nl": "NL", "netherlands": "NL", "the netherlands": "NL",
    "es": "ES", "spain": "ES", "it": "IT", "italy": "IT", "pt": "PT", "portugal": "PT",
    "pl": "PL", "poland": "PL", "se": "SE", "sweden": "SE", "dk": "DK", "denmark": "DK",
    "no": "NO", "norway": "NO", "fi": "FI", "finland": "FI", "ch": "CH", "switzerland": "CH",
    "at": "AT", "austria": "AT", "be": "BE", "belgium": "BE", "cz": "CZ", "czechia": "CZ",
    "au": "AU", "australia": "AU", "nz": "NZ", "new zealand": "NZ",
    "sg": "SG", "singapore": "SG", "in": "IN", "india": "IN", "jp": "JP", "japan": "JP",
    "mx": "MX", "mexico": "MX", "br": "BR", "brazil": "BR", "il": "IL", "israel": "IL",
}


# A free-text location is not a country field. Resolving a bare two-letter token there
# reads "Philadelphia, PA" as Panama and "Chicago, IL" as Israel, and a card claiming a
# country nothing checked is exactly what a country filter must not be handed. So the
# two-letter form is accepted only from a field the board labels as the country, and free
# text resolves through spelled-out names.
FREE_TEXT_COUNTRIES = {name: code for name, code in COUNTRY_ALIASES.items() if len(name) > 2}
FREE_TEXT_COUNTRIES["uk"] = "GB"

# Spelled-out US states, which is how these boards write a US location. "Georgia" is left
# out: it is a country as well as a state, and a location string does not say which.
US_STATE_NAMES = frozenset({
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut",
    "delaware", "district of columbia", "florida", "hawaii", "idaho", "illinois", "indiana",
    "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts",
    "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania", "puerto rico",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington", "washington dc", "west virginia", "wisconsin",
    "wyoming",
})


def normalize_country_code(value: Any) -> str:
    """Resolve a field the board labels as its country. Two-letter codes are accepted."""
    text = str(value or "").strip().strip(",")
    if not text:
        return "unknown"
    key = text.casefold()
    if key in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[key]
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return "unknown"


# US postal abbreviations. Every one of these also reads as an ISO country code somewhere
# (PA/Panama, IL/Israel, DE/Germany), which is why they are only honoured in the one shape
# that means a state: the last segment of a "City, ST" location. A bare "DE" standing alone
# stays unknown.
US_STATE_ABBREVIATIONS = frozenset({
    "al", "ak", "az", "ar", "ca", "co", "ct", "dc", "de", "fl", "ga", "hi", "ia", "id",
    "il", "in", "ks", "ky", "la", "ma", "md", "me", "mi", "mn", "mo", "ms", "mt", "nc",
    "nd", "ne", "nh", "nj", "nm", "nv", "ny", "oh", "ok", "or", "pa", "pr", "ri", "sc",
    "sd", "tn", "tx", "ut", "va", "vt", "wa", "wi", "wv", "wy",
})


def free_text_country(value: Any) -> str:
    """Resolve one segment of a free-text location. Spelled-out names only."""
    key = clean_text(value).strip(",").casefold()
    if not key:
        return "unknown"
    if key in FREE_TEXT_COUNTRIES:
        return FREE_TEXT_COUNTRIES[key]
    if key in US_STATE_NAMES:
        return "US"
    return "unknown"


def resolve_country_basis(explicit: Any = None, *free_text: Any) -> tuple[str, str | None]:
    """The country, and how it was arrived at, so an inference stays auditable.

    The labelled country field is read first. Free-text locations are then tried whole and
    by trailing comma-separated segment, because a location ends in its country or state:
    "Boston, MA, United States" ends in the country, "Charlottesville, Virginia" in the
    state name, and "Philadelphia, PA" in the state abbreviation.
    """
    resolved = normalize_country_code(explicit)
    if resolved != "unknown":
        return resolved, "board_country_field"
    for value in free_text:
        text = clean_text(value)
        if not text:
            continue
        resolved = free_text_country(text)
        if resolved != "unknown":
            return resolved, "location_name"
        segments = [piece.strip() for piece in text.split(",") if piece.strip()]
        for part in reversed(segments):
            resolved = free_text_country(part)
            if resolved != "unknown":
                return resolved, "location_name"
        # Only the trailing segment of a multi-part location, which is the shape that means
        # a state. Anywhere else a two-letter token is as likely to be a country.
        if len(segments) >= 2 and segments[-1].casefold() in US_STATE_ABBREVIATIONS:
            return "US", "us_state_abbreviation"
    return "unknown", None


def resolve_country(explicit: Any = None, *free_text: Any) -> str:
    return resolve_country_basis(explicit, *free_text)[0]


def normalize_arrangement(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values).casefold()
    if "remote" in text:
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if "onsite" in text or "on-site" in text or "on site" in text or "in office" in text:
        return "onsite"
    return "unknown"


def normalize_timestamp(value: Any) -> str | None:
    """Epoch milliseconds or an ISO-8601 string to a UTC ISO-8601 string."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        try:
            seconds = float(value) / 1000.0
        except (TypeError, ValueError):
            return None
        if not 0 < seconds < 4102444800:  # through 2100; anything else is not a posting date
            return None
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    return parsed.isoformat()


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clean_body(value: Any) -> str:
    """Collapse spacing inside a line while keeping the line breaks that carry structure."""
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in str(value or "").split("\n")]
    return "\n".join(line for line in lines if line).strip()


BLOCK_TAGS = frozenset({
    "address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "figure", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "ol", "p", "pre", "section",
    "table", "td", "th", "tr", "ul",
})
DROPPED_TAGS = frozenset({"script", "style", "noscript", "svg"})


class _BodyParser(HTMLParser):
    """Posting markup to text, one line per block.

    `ingest_job.clean_html` joins every text node with a space, which suits a JSON-LD blob
    but not an ATS body: those are mostly `<li>` and `<p>`, and bullets carry no terminal
    punctuation, so a space-join fuses a requirements section into one unsplittable
    sentence and the sponsorship scan quotes the section where it should quote the line.
    Splitting on every text node is equally wrong — inline `<b>` and `<a>` would break a
    sentence mid-clause — so only block boundaries end a line.
    """

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._dropped = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROPPED_TAGS:
            self._dropped += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in DROPPED_TAGS and self._dropped:
            self._dropped -= 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._dropped:
            self.parts.append(data)


def clean_markup(value: Any) -> str:
    parser = _BodyParser()
    parser.feed(str(value or ""))
    # HTMLParser already resolves character references in data, so nothing is unescaped here.
    return clean_body("".join(parser.parts))


def bounded_list(values: list[str], *, max_items: int, max_chars: int) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = clean_text(value)[:max_chars]
        if text and text not in seen:
            seen.append(text)
    return seen[:max_items]


# --- Adapters --------------------------------------------------------------
# Each adapter exposes: fetch(token, get) -> (postings, endpoints); summary(posting) ->
# the hard fields a discovery filter may read; detail_url(token, posting) -> str | None;
# card(source, posting, detail) -> partial JobCard fields.


def _greenhouse_fetch(token: str, get: Callable[[str], Any]) -> tuple[list[dict[str, Any]], list[str]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{quote(token, safe='')}/jobs?content=true"
    payload = get(url)
    if not isinstance(payload, dict):
        raise SourceError("greenhouse board did not return an object")
    return [item for item in (payload.get("jobs") or []) if isinstance(item, dict)], [url]


def _greenhouse_office_names(posting: dict[str, Any]) -> list[str]:
    return [str(office.get("name") or "") for office in (posting.get("offices") or [])
            if isinstance(office, dict)]


def _greenhouse_summary(posting: dict[str, Any]) -> dict[str, Any]:
    location = clean_text((posting.get("location") or {}).get("name")) or "unknown"
    deadline = normalize_timestamp(posting.get("application_deadline"))
    status = "open"
    if deadline and deadline[:10] < now_utc().date().isoformat():
        status = "closed"
    return {
        "external_id": str(posting.get("id") or ""),
        "title": clean_text(posting.get("title")),
        "location": location,
        **dict(zip(("country", "country_basis"), resolve_country_basis(None, *_greenhouse_office_names(posting), location))),
        "work_arrangement": normalize_arrangement(location),
        "employment_type": "unknown",
        "status": status,
        "posted_at": normalize_timestamp(posting.get("first_published")
                                        or posting.get("updated_at")),
    }


def _greenhouse_card(source: dict[str, Any], posting: dict[str, Any],
                     detail: dict[str, Any] | None) -> dict[str, Any]:
    summary = _greenhouse_summary(posting)
    departments = [str(item.get("name") or "") for item in (posting.get("departments") or [])
                   if isinstance(item, dict)]
    return {
        **summary,
        # Greenhouse returns the posting body HTML-escaped inside JSON, so it has to be
        # unescaped before it is parsed as markup; parsing it first yields literal tags.
        "description": clean_markup(unescape(str(posting.get("content") or ""))),
        "employer": clean_text(posting.get("company_name")) or source["company"],
        "canonical_url": str(posting.get("absolute_url") or ""),
        "apply_url": str(posting.get("absolute_url") or ""),
        "requisition_id": clean_text(posting.get("requisition_id")) or None,
        "salary": None,
        "compensation_structure": [],
        "department": next((item for item in departments if item), None),
        "team": None,
        "updated_at": normalize_timestamp(posting.get("updated_at")),
    }


def _lever_fetch(token: str, get: Callable[[str], Any]) -> tuple[list[dict[str, Any]], list[str]]:
    url = f"https://api.lever.co/v0/postings/{quote(token, safe='')}?mode=json"
    payload = get(url)
    if not isinstance(payload, list):
        raise SourceError("lever board did not return a list")
    return [item for item in payload if isinstance(item, dict)], [url]


def _lever_summary(posting: dict[str, Any]) -> dict[str, Any]:
    categories = posting.get("categories") or {}
    location = clean_text(categories.get("location")) or "unknown"
    return {
        "external_id": str(posting.get("id") or ""),
        "title": clean_text(posting.get("text")),
        "location": location,
        **dict(zip(("country", "country_basis"), resolve_country_basis(posting.get("country"), location))),
        "work_arrangement": normalize_arrangement(posting.get("workplaceType"), location),
        "employment_type": ingest_job.normalize_employment(categories.get("commitment")),
        "status": "open",  # a Lever feed lists open postings only
        "posted_at": normalize_timestamp(posting.get("createdAt")),
    }


def _lever_card(source: dict[str, Any], posting: dict[str, Any],
                detail: dict[str, Any] | None) -> dict[str, Any]:
    categories = posting.get("categories") or {}
    # `description` already contains opening + body; the lists carry the requirements,
    # and `additional` the closing sections. Dropping either loses the qualifications.
    parts = [str(posting.get("description") or "")]
    for item in posting.get("lists") or []:
        if isinstance(item, dict):
            parts.append(f"<p>{item.get('text', '')}</p><ul>{item.get('content', '')}</ul>")
    parts.append(str(posting.get("additional") or ""))
    return {
        **_lever_summary(posting),
        "description": clean_markup("".join(parts)),
        "employer": source["company"],  # a Lever posting does not name its company
        "canonical_url": str(posting.get("hostedUrl") or ""),
        "apply_url": str(posting.get("applyUrl") or posting.get("hostedUrl") or ""),
        "requisition_id": None,
        "salary": None,
        "compensation_structure": [],
        "department": clean_text(categories.get("department")) or None,
        "team": clean_text(categories.get("team")) or None,
        "updated_at": None,
    }


def _ashby_fetch(token: str, get: Callable[[str], Any]) -> tuple[list[dict[str, Any]], list[str]]:
    url = (f"https://api.ashbyhq.com/posting-api/job-board/{quote(token, safe='')}"
           "?includeCompensation=true")
    payload = get(url)
    if not isinstance(payload, dict):
        raise SourceError("ashby board did not return an object")
    return [item for item in (payload.get("jobs") or []) if isinstance(item, dict)], [url]


def _ashby_summary(posting: dict[str, Any]) -> dict[str, Any]:
    location = clean_text(posting.get("location")) or "unknown"
    country_field = (((posting.get("address") or {}).get("postalAddress") or {})
                     .get("addressCountry"))
    return {
        "external_id": str(posting.get("id") or ""),
        "title": clean_text(posting.get("title")),
        "location": location,
        **dict(zip(("country", "country_basis"), resolve_country_basis(country_field, location))),
        "work_arrangement": ("remote" if posting.get("isRemote")
                             else normalize_arrangement(posting.get("workplaceType"), location)),
        "employment_type": ingest_job.normalize_employment(posting.get("employmentType")),
        "status": "open" if posting.get("isListed") else "closed",
        "posted_at": normalize_timestamp(posting.get("publishedAt")),
    }


def _ashby_compensation(posting: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """Structured salary only when the board states exactly one salary band.

    Several tiers mean several locations. Picking one silently would put a number on the
    card that the posting never attached to this candidate's location, so the tiers are
    kept as reviewable text and `salary` stays null.
    """
    compensation = posting.get("compensation") or {}
    salaries: list[dict[str, Any]] = []
    extras: list[str] = []
    for tier in compensation.get("compensationTiers") or []:
        if not isinstance(tier, dict):
            continue
        for component in tier.get("components") or []:
            if not isinstance(component, dict):
                continue
            summary = clean_text(component.get("summary"))
            if str(component.get("compensationType")) == "Salary":
                if component.get("minValue") is None and component.get("maxValue") is None:
                    continue
                interval = str(component.get("interval") or "1 YEAR").split()[-1].upper()
                salaries.append({"currency": component.get("currencyCode"),
                                 "min": component.get("minValue"),
                                 "max": component.get("maxValue"),
                                 "unit": interval or "YEAR"})
            elif summary:
                extras.append(summary)
    tier_summary = clean_text(compensation.get("compensationTierSummary"))
    unique = {json.dumps(item, sort_keys=True) for item in salaries}
    if len(unique) == 1:
        return salaries[0], bounded_list(extras, max_items=MAX_COMPENSATION_ITEMS,
                                         max_chars=MAX_COMPENSATION_CHARS), []
    notes = ["multiple_compensation_tiers"] if len(unique) > 1 else []
    listed = ([tier_summary] if tier_summary else []) + extras
    return None, bounded_list(listed, max_items=MAX_COMPENSATION_ITEMS,
                              max_chars=MAX_COMPENSATION_CHARS), notes


def _ashby_card(source: dict[str, Any], posting: dict[str, Any],
                detail: dict[str, Any] | None) -> dict[str, Any]:
    salary, compensation_structure, notes = _ashby_compensation(posting)
    return {
        **_ashby_summary(posting),
        "description": clean_body(posting.get("descriptionPlain")
                                  or clean_markup(posting.get("descriptionHtml"))),
        "employer": source["company"],  # an Ashby posting does not name its company
        "canonical_url": str(posting.get("jobUrl") or ""),
        "apply_url": str(posting.get("applyUrl") or posting.get("jobUrl") or ""),
        "requisition_id": None,
        "salary": salary,
        "compensation_structure": compensation_structure,
        "department": clean_text(posting.get("department")) or None,
        "team": clean_text(posting.get("team")) or None,
        "updated_at": None,
        "notes": notes,
    }


def _smartrecruiters_fetch(token: str, get: Callable[[str], Any]) -> tuple[list[dict[str, Any]], list[str]]:
    postings: list[dict[str, Any]] = []
    endpoints: list[str] = []
    offset = 0
    for _ in range(MAX_PAGES):
        url = (f"https://api.smartrecruiters.com/v1/companies/{quote(token, safe='')}"
               f"/postings?limit={PAGE_SIZE}&offset={offset}")
        payload = get(url)
        endpoints.append(url)
        if not isinstance(payload, dict):
            raise SourceError("smartrecruiters board did not return an object")
        page = [item for item in (payload.get("content") or []) if isinstance(item, dict)]
        postings.extend(page)
        offset += len(page)
        total = payload.get("totalFound")
        if not page or (isinstance(total, int) and offset >= total) or len(postings) >= MAX_POSTINGS:
            break
    return postings, endpoints


def _smartrecruiters_summary(posting: dict[str, Any]) -> dict[str, Any]:
    location = posting.get("location") or {}
    text = clean_text(location.get("fullLocation")) or clean_text(
        ", ".join(part for part in (location.get("city"), location.get("region")) if part)) or "unknown"
    arrangement = "unknown"
    if location.get("remote"):
        arrangement = "remote"
    elif location.get("hybrid"):
        arrangement = "hybrid"
    return {
        "external_id": str(posting.get("id") or ""),
        "title": clean_text(posting.get("name")),
        "location": text,
        **dict(zip(("country", "country_basis"), resolve_country_basis(location.get("country"), text))),
        "work_arrangement": arrangement if arrangement != "unknown" else normalize_arrangement(text),
        "employment_type": ingest_job.normalize_employment(
            (posting.get("typeOfEmployment") or {}).get("label")),
        "status": "open" if str(posting.get("visibility") or "PUBLIC").upper() == "PUBLIC" else "closed",
        "posted_at": normalize_timestamp(posting.get("releasedDate")),
    }


def _smartrecruiters_detail_url(token: str, posting: dict[str, Any]) -> str:
    return (f"https://api.smartrecruiters.com/v1/companies/{quote(token, safe='')}"
            f"/postings/{quote(str(posting.get('id') or ''), safe='')}")


SMARTRECRUITERS_SECTIONS = ("jobDescription", "qualifications", "additionalInformation",
                            "companyDescription")


def _smartrecruiters_card(source: dict[str, Any], posting: dict[str, Any],
                          detail: dict[str, Any] | None) -> dict[str, Any]:
    detail = detail or {}
    sections = ((detail.get("jobAd") or {}).get("sections") or {})
    ordered = list(SMARTRECRUITERS_SECTIONS) + sorted(set(sections) - set(SMARTRECRUITERS_SECTIONS))
    parts: list[str] = []
    for name in ordered:
        block = sections.get(name)
        if isinstance(block, dict) and block.get("text"):
            parts.append(f"<p>{block.get('title', name)}</p>{block['text']}")
    summary = _smartrecruiters_summary(posting)
    if detail.get("active") is False:
        summary["status"] = "closed"
    return {
        **summary,
        "description": clean_markup("".join(parts)),
        "employer": clean_text((posting.get("company") or {}).get("name")) or source["company"],
        "canonical_url": str(detail.get("postingUrl") or posting.get("ref") or ""),
        "apply_url": str(detail.get("applyUrl") or detail.get("postingUrl") or ""),
        "requisition_id": clean_text(posting.get("refNumber")) or None,
        "salary": None,
        "compensation_structure": [],
        "department": clean_text((posting.get("department") or {}).get("label")) or None,
        "team": clean_text((posting.get("function") or {}).get("label")) or None,
        "updated_at": None,
    }


ADAPTERS: dict[str, dict[str, Any]] = {
    "greenhouse": {
        "fetch": _greenhouse_fetch,
        "summary": _greenhouse_summary,
        "detail_url": None,
        "card": _greenhouse_card,
        "board_url": lambda token: f"https://job-boards.greenhouse.io/{token}",
        "endpoint_template": "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true",
        "docs": "https://developers.greenhouse.io/job-board.html",
        "authorization": PUBLIC_JOB_BOARD_API,
    },
    "lever": {
        "fetch": _lever_fetch,
        "summary": _lever_summary,
        "detail_url": None,
        "card": _lever_card,
        "board_url": lambda token: f"https://jobs.lever.co/{token}",
        "endpoint_template": "https://api.lever.co/v0/postings/{board_token}?mode=json",
        "docs": "https://github.com/lever/postings-api",
        "authorization": PUBLIC_JOB_BOARD_API,
    },
    "ashby": {
        "fetch": _ashby_fetch,
        "summary": _ashby_summary,
        "detail_url": None,
        "card": _ashby_card,
        "board_url": lambda token: f"https://jobs.ashbyhq.com/{token}",
        "endpoint_template": "https://api.ashbyhq.com/posting-api/job-board/{board_token}?includeCompensation=true",
        "docs": "https://developers.ashbyhq.com/docs/public-job-posting-api",
        "authorization": PUBLIC_JOB_BOARD_API,
    },
    "smartrecruiters": {
        "fetch": _smartrecruiters_fetch,
        "summary": _smartrecruiters_summary,
        "detail_url": _smartrecruiters_detail_url,
        "card": _smartrecruiters_card,
        "board_url": lambda token: f"https://jobs.smartrecruiters.com/{token}",
        "endpoint_template": ("https://api.smartrecruiters.com/v1/companies/{board_token}"
                              "/postings?limit=100&offset=0"),
        "docs": "https://developers.smartrecruiters.com/reference/postingapisearch",
        "authorization": PUBLIC_JOB_BOARD_API,
    },
}
SUPPORTED_ATS = tuple(sorted(ADAPTERS))


# --- Transport -------------------------------------------------------------


def fetch_json(url: str) -> Any:
    """GET a public job-board endpoint. No cookies, no credentials, no redirect chasing."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as error:
        raise SourceError(f"{url} returned HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise SourceError(f"{url} could not be reached: {error.reason}") from error
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise SourceError(f"{url} did not return JSON") from error


# --- Source registry -------------------------------------------------------


def registry_path(private_root: Path) -> Path:
    return Path(private_root) / REGISTRY_FILENAME


def empty_registry() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "sources": []}


def load_registry(path: Path) -> dict[str, Any]:
    if not Path(path).exists():
        return empty_registry()
    registry = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(registry, dict) or not isinstance(registry.get("sources"), list):
        raise ValueError(f"malformed ATS source registry: {path}")
    return registry


def save_registry(path: Path, registry: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def validate_source_identity(ats: str, board_token: str) -> tuple[str, str]:
    ats = str(ats or "").strip().casefold()
    if ats not in ADAPTERS:
        raise ValueError(f"unsupported ats: {ats or '(empty)'}; supported: {', '.join(SUPPORTED_ATS)}")
    token = str(board_token or "").strip()
    # The token is interpolated into a URL. Anything outside this class could redirect the
    # pull at a host or path the registry never recorded.
    if not BOARD_TOKEN_PATTERN.fullmatch(token):
        raise ValueError("board token must be 1-100 characters of letters, digits, dot, dash, or underscore")
    return ats, token


def find_source(registry: dict[str, Any], ats: str, board_token: str) -> dict[str, Any] | None:
    ats, board_token = validate_source_identity(ats, board_token)
    return next((source for source in registry["sources"]
                 if source.get("ats") == ats and source.get("board_token") == board_token), None)


def add_source(registry: dict[str, Any], company: str, ats: str, board_token: str, actor: str,
               *, verification: dict[str, Any] | None = None, note: str | None = None,
               compliance_basis: str | None = None, known_risks: str | None = None,
               at: datetime | None = None) -> dict[str, Any]:
    ats, board_token = validate_source_identity(ats, board_token)
    company = clean_text(company)
    if not company or len(company) > 200:
        raise ValueError("company must be a non-empty name of at most 200 characters")
    if not clean_text(actor):
        raise ValueError("actor is required: a registered source records who authorized it")
    if find_source(registry, ats, board_token):
        raise ValueError(f"source already registered: {ats}/{board_token}")
    adapter = ADAPTERS[ats]
    authorization = adapter["authorization"]
    spec = AUTHORIZATIONS[authorization]
    # A source whose compliance rests on the operator's own judgement has to record that
    # judgement. It is the operator carrying the risk, so the reasoning cannot stay implicit
    # — the same discipline the repo applies to every other deliberate exception.
    justification: dict[str, Any] | None = None
    if spec["requires_operator_justification"]:
        if not clean_text(compliance_basis) or not clean_text(known_risks):
            raise ValueError(
                f"{authorization} sources require compliance_basis and known_risks: the "
                "operator carries this compliance judgement, so it must be written down")
        justification = {"compliance_basis": clean_text(compliance_basis),
                         "known_risks": clean_text(known_risks)}
    elif compliance_basis or known_risks:
        raise ValueError(f"{authorization} sources take no operator justification: the "
                         "platform's own terms are the basis, and recording a private "
                         "rationale beside them would blur which one applies")
    timestamp = (at or now_utc()).isoformat()
    source = {
        "company": company,
        "ats": ats,
        "board_token": board_token,
        "enabled": True,
        "board_url": adapter["board_url"](board_token),
        "note": clean_text(note) or None,
        "registered_at": timestamp,
        "registered_by": clean_text(actor),
        "authorization": {
            "basis": authorization,
            "tier": spec["tier"],
            "platform_permitted": spec["platform_permitted"],
            "endpoint_template": adapter["endpoint_template"],
            "docs": adapter["docs"],
            "credentials_used": False,
            "verified_at": (verification or {}).get("verified_at"),
            "verified_posting_count": (verification or {}).get("postings"),
            **({"operator_justification": justification} if justification else {}),
        },
    }
    registry["sources"].append(source)
    registry["sources"].sort(key=lambda item: (item.get("company", "").casefold(),
                                              item.get("ats", ""), item.get("board_token", "")))
    return source


def set_source_enabled(registry: dict[str, Any], ats: str, board_token: str,
                       enabled: bool) -> dict[str, Any]:
    source = find_source(registry, ats, board_token)
    if source is None:
        raise ValueError(f"source not registered: {ats}/{board_token}")
    source["enabled"] = bool(enabled)
    return source


def remove_source(registry: dict[str, Any], ats: str, board_token: str) -> dict[str, Any]:
    source = find_source(registry, ats, board_token)
    if source is None:
        raise ValueError(f"source not registered: {ats}/{board_token}")
    registry["sources"].remove(source)
    return source


def list_sources(registry: dict[str, Any], *, company: str | None = None,
                 include_disabled: bool = True) -> list[dict[str, Any]]:
    wanted = clean_text(company).casefold() if company else None
    return [source for source in registry["sources"]
            if (include_disabled or source.get("enabled"))
            and (wanted is None or source.get("company", "").casefold() == wanted)]


def probe(ats: str, board_token: str, *, fetch: Callable[[str], Any] | None = None,
          at: datetime | None = None) -> dict[str, Any]:
    """Read a board without registering it, so a token can be checked before it is trusted."""
    ats, board_token = validate_source_identity(ats, board_token)
    get = fetch or fetch_json
    adapter = ADAPTERS[ats]
    postings, endpoints = adapter["fetch"](board_token, get)
    summaries = [adapter["summary"](posting) for posting in postings[:MAX_POSTINGS]]
    return {
        "ats": ats, "board_token": board_token, "resolved": True,
        "endpoints": endpoints, "postings": len(postings),
        "open_postings": sum(1 for item in summaries if item["status"] == "open"),
        "sample_titles": [item["title"] for item in summaries[:5] if item["title"]],
        "checked_at": (at or now_utc()).isoformat(),
    }


def probe_token(board_token: str, *, fetch: Callable[[str], Any] | None = None,
                at: datetime | None = None) -> dict[str, Any]:
    """Try one slug against every supported ATS: the cheap way to find a company's board."""
    results = []
    for ats in SUPPORTED_ATS:
        try:
            results.append(probe(ats, board_token, fetch=fetch, at=at))
        except SourceError as error:
            results.append({"ats": ats, "board_token": board_token, "resolved": False,
                            "error": str(error)})
    # An endpoint answering 200 with an empty list is not evidence the company's board is
    # here: some of these APIs answer that way for a slug that was never registered. Only
    # a board that actually returned postings is reported as found.
    return {
        "board_token": board_token,
        "results": results,
        "resolved": [item["ats"] for item in results if item.get("postings")],
        "answered_empty": [item["ats"] for item in results
                           if item.get("resolved") and not item.get("postings")],
    }


# --- JobCard construction --------------------------------------------------

# --- Structuring -----------------------------------------------------------
# `direction_core` never reads `description` — it is on the routing denylist — so a card
# whose evidence lives only in prose is routed on its title alone. Measured over 1,199
# openings from these boards, that is the difference between 8 and 119 reaching review.
# The same rules-only extractor the browser path uses turns the parts a posting states
# outright into the fields routing is allowed to read, already fitted to the routing shapes.
STRUCTURED_FROM_PROSE = ("required_skills", "preferred_skills", "responsibilities",
                         "compensation_structure")


def structure_card(card: dict[str, Any], *, extract: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Fill the routable fields from the posting's own prose.

    What the board stated in a labelled field always wins. The extractor reads prose, and
    prose is the weaker source: it may only fill what the board left empty or unknown. The
    card stays `requirements_reviewed: false` either way — structuring proposes, it does
    not review.
    """
    reader = extract or posting_sections.extract
    try:
        found = reader(card["description"], title=card["title"]) or {}
    except Exception as error:  # a posting that will not parse is not a failed pull
        card["extraction"]["notes"].append("section_extraction_failed")
        card["extraction"]["sections"] = {"error": str(error)[:200]}
        return card
    for field in STRUCTURED_FROM_PROSE:
        if not card.get(field) and found.get(field):
            card[field] = posting_sections.fit_routing_shape(found[field], field)
    if card.get("salary") is None and isinstance(found.get("salary"), dict):
        card["salary"] = found["salary"]
    for field in ("work_arrangement", "employment_type", "sponsorship"):
        value = found.get(field)
        if card.get(field) in (None, "", "unknown") and value and value != "unknown":
            card[field] = value
    report = dict(found.get("extraction") or {})
    # The verbatim requirement lines are what a reviewer needs and are not routable, so
    # they travel in the extraction report rather than in a routed field.
    for key in ("required_skills_stated", "preferred_skills_stated",
                "required_skills_capabilities", "preferred_skills_capabilities"):
        if found.get(key):
            # Verbatim and uncapped: these are what a reviewer reads, they are never routed,
            # and shortening the line the employer actually wrote defeats their purpose.
            report[key] = found[key]
    card["extraction"]["sections"] = report
    return card




def build_card(source: dict[str, Any], fields: dict[str, Any], *,
               endpoint: str, at: datetime | None = None,
               extract: Callable[..., Any] | None = None) -> dict[str, Any]:
    timestamp = (at or now_utc()).isoformat()
    notes = list(fields.get("notes") or [])
    description = clean_body(fields.get("description"))
    if len(description) > MAX_DESCRIPTION_CHARS:
        description = description[:MAX_DESCRIPTION_CHARS]
        notes.append("description_truncated")
    title = clean_text(fields.get("title"))[:MAX_TITLE_CHARS] or "unknown"
    external_id = str(fields.get("external_id") or "")
    if not external_id:
        raise SourceError("posting has no ATS identifier")
    # Identity is the ATS posting itself, not the rendered URL or title, so a re-titled or
    # re-hosted posting still resolves to the same job on the next pull.
    seed = f"{source['ats']}|{source['board_token']}|{external_id}".encode("utf-8")
    # The basis frozen at registration, not whatever the adapter says today. Reading the
    # adapter here would mean that editing one line of `ADAPTERS` silently relabels every
    # card from every source already registered against it — the exact upgrade this design
    # refuses. A source that genuinely gains platform permission is registered again under
    # the new basis, which is a new entry, not a relabelled old one, and only that
    # re-registration may change what a card claims.
    #
    # An unregistered source (a `probe`, a test) falls back to the adapter's own basis,
    # which is the most it can honestly say about itself.
    authorization = (((source.get("authorization") or {}).get("basis"))
                     or ADAPTERS[source["ats"]]["authorization"])
    if authorization not in AUTHORIZATIONS:
        raise SourceError(f"source records an unknown authorization basis: {authorization}")
    statements, sponsorship_scan = ingest_job.sponsorship_statements(description)
    canonical_url = str(fields.get("canonical_url") or "").strip()
    if fields.get("country_basis") == "us_state_abbreviation":
        notes.append("country_inferred_from_state_abbreviation")
    card = {
        "schema_version": CARD_SCHEMA_VERSION,
        "job_id": f"job-{hashlib.sha256(seed).hexdigest()[:12]}",
        "canonical_url": canonical_url or "unknown",
        "employer": clean_text(fields.get("employer")) or source["company"],
        "title": title,
        "country": fields.get("country") or "unknown",
        "location": clean_text(fields.get("location")) or "unknown",
        "work_arrangement": fields.get("work_arrangement") or "unknown",
        "employment_type": fields.get("employment_type") or "unknown",
        "salary": fields.get("salary"),
        "status": fields.get("status") or "unknown",
        "sponsorship": "unknown",
        "citizenship_required": None,
        "security_clearance_required": None,
        "required_certifications": [],
        "preferred_certifications": [],
        # An ATS board publishes prose, not a structured requirement list. Leaving these
        # empty keeps the card honest; the reviewer fills them when marking it reviewed.
        "required_skills": [],
        "preferred_skills": [],
        "summary": None,
        "responsibilities": [],
        "compensation_structure": bounded_list(fields.get("compensation_structure") or [],
                                               max_items=MAX_COMPENSATION_ITEMS,
                                               max_chars=MAX_COMPENSATION_CHARS),
        "sponsorship_statements": statements,
        "seniority": "unknown",
        "experience": None,
        "already_applied": False,
        "high_value": False,
        "requirements_reviewed": False,
        "source": "ats",
        "ats": source["ats"],
        # On the card, not looked up from the registry at submission time. The registry
        # changes — a source can be disabled, removed, or re-registered under a different
        # basis — and an archived card has to still say six months later how it was read.
        # This is the same rule the resume archive follows: copy the bytes, never a pointer.
        "authorization": authorization,
        "source_tier": AUTHORIZATIONS[authorization]["tier"],
        "platform_permitted": AUTHORIZATIONS[authorization]["platform_permitted"],
        "requisition_id": fields.get("requisition_id"),
        "description": description,
        "description_sha256": hashlib.sha256(description.encode("utf-8")).hexdigest(),
        "extraction": {
            "strategy": f"ats_api:{source['ats']}",
            "needs_user_review": True,
            "sponsorship_scan": sponsorship_scan,
            "notes": notes,
            "ats": {
                "company": source["company"],
                "board_token": source["board_token"],
                "external_id": external_id,
                "endpoint": endpoint,
                "apply_url": str(fields.get("apply_url") or "") or None,
                "posted_at": fields.get("posted_at"),
                "updated_at": fields.get("updated_at"),
                "department": fields.get("department"),
                "team": fields.get("team"),
                "fetched_at": timestamp,
            },
        },
    }
    return structure_card(card, extract=extract)


# --- Discovery filters -----------------------------------------------------
# Hard, checkable facts only. Nothing here decides whether a posting is a good fit; that
# is `direction_core.route_job`, working from a reviewed card.
FILTER_KEYS = ("title_contains", "title_excludes", "location_contains", "countries",
               "work_arrangements", "employment_types", "posted_since", "include_closed")


def normalize_filters(filters: dict[str, Any] | None) -> dict[str, Any]:
    filters = dict(filters or {})
    unknown = sorted(set(filters) - set(FILTER_KEYS))
    if unknown:
        raise ValueError(f"unsupported pull filters: {', '.join(unknown)}")
    normalized: dict[str, Any] = {
        key: [clean_text(item).casefold() for item in (filters.get(key) or []) if clean_text(item)]
        for key in ("title_contains", "title_excludes", "location_contains")
    }
    normalized["countries"] = [normalize_country_code(item) for item in (filters.get("countries") or [])]
    normalized["work_arrangements"] = [clean_text(item).casefold()
                                       for item in (filters.get("work_arrangements") or [])]
    normalized["employment_types"] = [clean_text(item).casefold()
                                      for item in (filters.get("employment_types") or [])]
    since = filters.get("posted_since")
    if since:
        normalized["posted_since"] = date.fromisoformat(str(since)[:10]).isoformat()
    else:
        normalized["posted_since"] = None
    normalized["include_closed"] = bool(filters.get("include_closed"))
    return normalized


def _drop_reason(summary: dict[str, Any], filters: dict[str, Any]) -> str | None:
    title = summary["title"].casefold()
    location = summary["location"].casefold()
    if not filters["include_closed"] and summary["status"] != "open":
        return "status_not_open"
    if filters["title_contains"] and not any(term in title for term in filters["title_contains"]):
        return "title_contains"
    if any(term in title for term in filters["title_excludes"]):
        return "title_excludes"
    if filters["location_contains"] and not any(term in location
                                                for term in filters["location_contains"]):
        return "location_contains"
    if filters["countries"] and summary["country"] not in filters["countries"]:
        return "country"
    if filters["work_arrangements"] and summary["work_arrangement"] not in filters["work_arrangements"]:
        return "work_arrangement"
    if filters["employment_types"] and summary["employment_type"] not in filters["employment_types"]:
        return "employment_type"
    if filters["posted_since"] and summary["posted_at"] and summary["posted_at"][:10] < filters["posted_since"]:
        return "posted_since"
    return None


def apply_filters(summaries: list[dict[str, Any]], filters: dict[str, Any]) -> tuple[list[int], dict[str, Any]]:
    kept: list[int] = []
    dropped: dict[str, int] = {}
    undated_kept = 0
    for index, summary in enumerate(summaries):
        reason = _drop_reason(summary, filters)
        if reason:
            dropped[reason] = dropped.get(reason, 0) + 1
            continue
        if filters["posted_since"] and not summary["posted_at"]:
            undated_kept += 1
        kept.append(index)
    return kept, {"seen": len(summaries), "kept": len(kept),
                  "dropped": dict(sorted(dropped.items())),
                  "undated_kept": undated_kept}


# --- Pull ------------------------------------------------------------------


def _repetition(cards: list[dict[str, Any]]) -> dict[str, int]:
    """How much of a pull is one role re-posted per location.

    A board that lists one opening in forty cities returns forty postings, and a report
    that says only "kept 40" reads as forty openings. The job store still collapses them —
    duplicate detection is its job, not the puller's — but the inflation should be visible
    before the cards are written, not inferred afterwards from the ingest counts.
    """
    fingerprints = {card["description_sha256"] for card in cards}
    return {"distinct_descriptions": len(fingerprints),
            "repeated_postings": len(cards) - len(fingerprints)}


def pull_source(source: dict[str, Any], *, fetch: Callable[[str], Any] | None = None,
                filters: dict[str, Any] | None = None, limit: int | None = None,
                at: datetime | None = None, sleep: Callable[[float], None] | None = None,
                detail_delay: float = DETAIL_REQUEST_DELAY) -> dict[str, Any]:
    ats, board_token = validate_source_identity(source.get("ats"), source.get("board_token"))
    company = clean_text(source.get("company"))
    if not company:
        raise ValueError("source is missing a company name")
    # The registry row's own authorization travels with the identity, so the card records
    # how this source was authorized when it was registered rather than how its adapter is
    # configured now.
    identity = {"company": company, "ats": ats, "board_token": board_token,
                "authorization": source.get("authorization")}
    adapter = ADAPTERS[ats]
    get = fetch or fetch_json
    pause = sleep if sleep is not None else time.sleep
    active = normalize_filters(filters)
    timestamp = (at or now_utc()).isoformat()

    postings, endpoints = adapter["fetch"](board_token, get)
    board_truncated = len(postings) > MAX_POSTINGS
    postings = postings[:MAX_POSTINGS]
    summaries = [adapter["summary"](posting) for posting in postings]
    kept, report = apply_filters(summaries, active)
    report["board_truncated"] = board_truncated
    report["limit"] = limit
    report["limit_truncated"] = bool(limit is not None and len(kept) > limit)
    if report["limit_truncated"]:
        report["dropped_by_limit"] = len(kept) - int(limit)
        kept = kept[:limit]

    cards: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for position, index in enumerate(kept):
        posting = postings[index]
        detail = None
        endpoint = endpoints[0] if endpoints else adapter["endpoint_template"]
        try:
            if adapter["detail_url"]:
                if position and detail_delay:
                    pause(detail_delay)
                endpoint = adapter["detail_url"](board_token, posting)
                detail = get(endpoint)
                if not isinstance(detail, dict):
                    raise SourceError("posting detail did not return an object")
            cards.append(build_card(identity, adapter["card"](identity, posting, detail),
                                    endpoint=endpoint, at=at))
        except (SourceError, ValueError, TypeError, KeyError) as error:
            errors.append({"external_id": summaries[index]["external_id"],
                           "title": summaries[index]["title"], "error": str(error)})
    return {
        "schema_version": SCHEMA_VERSION,
        **identity,
        "fetched_at": timestamp,
        "endpoints": endpoints,
        "filters": active,
        "report": {**report, "cards_built": len(cards), "errors": len(errors),
                   **_repetition(cards)},
        "errors": errors,
        "cards": cards,
    }


def pull_sources(sources: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    results = []
    for source in sources:
        try:
            results.append(pull_source(source, **kwargs))
        except (SourceError, ValueError) as error:
            results.append({"schema_version": SCHEMA_VERSION, "company": source.get("company"),
                            "ats": source.get("ats"), "board_token": source.get("board_token"),
                            "failed": True, "error": str(error), "cards": []})
    return {
        "schema_version": SCHEMA_VERSION,
        "sources": len(sources),
        "failed_sources": sum(1 for item in results if item.get("failed")),
        "cards_built": sum(len(item.get("cards") or []) for item in results),
        "results": results,
    }


def write_cards(cards: list[dict[str, Any]], output_dir: Path) -> list[dict[str, Any]]:
    """Write one card per posting, reporting what actually changed since the last pull."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)
    written = []
    for card in cards:
        path = output_dir / f"{card['job_id']}.json"
        state = "created"
        if path.exists():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                previous = {}
            state = ("unchanged" if previous.get("description_sha256") == card["description_sha256"]
                     else "updated")
        path.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.chmod(path, 0o600)
        written.append({"job_id": card["job_id"], "title": card["title"],
                        "canonical_url": card["canonical_url"], "path": str(path), "state": state})
    return written


def ingest_cards(connection: Any, cards: list[dict[str, Any]], *,
                 allow_possible_duplicate: bool = False,
                 at: datetime | None = None) -> dict[str, Any]:
    """Hand pulled cards to the existing job store, which owns duplicate detection."""
    decisions: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for card in cards:
        outcome = application_core.ingest_job(connection, card,
                                              allow_possible_duplicate=allow_possible_duplicate, at=at)
        counts[outcome["decision"]] = counts.get(outcome["decision"], 0) + 1
        decisions.append({"job_id": card["job_id"], "title": card["title"],
                          "decision": outcome["decision"], "reason": outcome.get("reason"),
                          "matched_job_id": outcome.get("job_id")})
    return {"decisions": decisions, "counts": dict(sorted(counts.items()))}


# --- CLI -------------------------------------------------------------------


def _selected_sources(registry: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.all:
        sources = list_sources(registry, include_disabled=False)
        if not sources:
            raise ValueError("no enabled sources registered")
        return sources
    if args.ats and args.board_token:
        source = find_source(registry, args.ats, args.board_token)
        if source is None:
            raise ValueError(f"source not registered: {args.ats}/{args.board_token}")
        return [source]
    if args.company:
        sources = list_sources(registry, company=args.company, include_disabled=False)
        if not sources:
            raise ValueError(f"no enabled source registered for company: {args.company}")
        return sources
    raise ValueError("select sources with --all, --company, or --ats with --board-token")


def _filters_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "title_contains": args.title_contains, "title_excludes": args.title_excludes,
        "location_contains": args.location_contains, "countries": args.country,
        "work_arrangements": args.work_arrangement, "employment_types": args.employment_type,
        "posted_since": args.posted_since, "include_closed": args.include_closed,
    }


def _add_filter_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group(
        "discovery filters",
        "Hard, checkable narrowing of what is fetched. Never a routing or fit decision.")
    group.add_argument("--title-contains", action="append", default=[])
    group.add_argument("--title-excludes", action="append", default=[])
    group.add_argument("--location-contains", action="append", default=[])
    group.add_argument("--country", action="append", default=[])
    group.add_argument("--work-arrangement", action="append", default=[],
                       choices=["remote", "hybrid", "onsite", "unknown"])
    group.add_argument("--employment-type", action="append", default=[],
                       choices=["full_time", "part_time", "contract", "unknown"])
    group.add_argument("--posted-since", help="ISO date; undated postings are kept and counted")
    group.add_argument("--include-closed", action="store_true")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--private-root", type=Path, default=Path(".jobloom"))
    commands = parser.add_subparsers(dest="command", required=True)

    probe_parser = commands.add_parser("probe", help="check a board token without registering it")
    probe_parser.add_argument("--board-token", required=True)
    probe_parser.add_argument("--ats", choices=SUPPORTED_ATS,
                              help="omit to try the token against every supported ATS")

    add_parser = commands.add_parser("add-source", help="register a company board")
    add_parser.add_argument("--company", required=True)
    add_parser.add_argument("--ats", required=True, choices=SUPPORTED_ATS)
    add_parser.add_argument("--board-token", required=True)
    add_parser.add_argument("--actor", required=True)
    add_parser.add_argument("--note")
    add_parser.add_argument("--compliance-basis",
                            help="self_asserted sources only: why the operator accepts reading it")
    add_parser.add_argument("--known-risks",
                            help="self_asserted sources only: when it stops being readable or acceptable")
    add_parser.add_argument("--skip-verify", action="store_true",
                            help="register without reading the board once first")

    list_parser = commands.add_parser("list-sources")
    list_parser.add_argument("--company")
    list_parser.add_argument("--enabled-only", action="store_true")

    for name in ("enable-source", "disable-source", "remove-source"):
        sub = commands.add_parser(name)
        sub.add_argument("--ats", required=True, choices=SUPPORTED_ATS)
        sub.add_argument("--board-token", required=True)

    pull_parser = commands.add_parser("pull", help="pull postings into reviewable JobCards")
    selection = pull_parser.add_argument_group("source selection")
    selection.add_argument("--all", action="store_true")
    selection.add_argument("--company")
    selection.add_argument("--ats", choices=SUPPORTED_ATS)
    selection.add_argument("--board-token")
    _add_filter_arguments(pull_parser)
    pull_parser.add_argument("--limit", type=int, help="cap kept postings per source; the cap is reported")
    pull_parser.add_argument("--output", type=Path, help="directory for one JobCard per posting")
    pull_parser.add_argument("--db", type=Path, help="also ingest the cards into the job store")
    pull_parser.add_argument("--allow-possible-duplicate", action="store_true")
    pull_parser.add_argument("--print-cards", action="store_true")

    args = parser.parse_args()
    path = registry_path(args.private_root)

    if args.command == "probe":
        result = (probe(args.ats, args.board_token) if args.ats
                  else probe_token(args.board_token))
    elif args.command == "add-source":
        registry = load_registry(path)
        verification = None
        if not args.skip_verify:
            checked = probe(args.ats, args.board_token)
            verification = {"verified_at": checked["checked_at"], "postings": checked["postings"]}
        result = add_source(registry, args.company, args.ats, args.board_token, args.actor,
                            verification=verification, note=args.note,
                            compliance_basis=args.compliance_basis, known_risks=args.known_risks)
        save_registry(path, registry)
    elif args.command == "list-sources":
        registry = load_registry(path)
        sources = list_sources(registry, company=args.company,
                               include_disabled=not args.enabled_only)
        result = {"schema_version": SCHEMA_VERSION, "registry": str(path),
                  "count": len(sources), "sources": sources}
    elif args.command in {"enable-source", "disable-source"}:
        registry = load_registry(path)
        result = set_source_enabled(registry, args.ats, args.board_token,
                                    args.command == "enable-source")
        save_registry(path, registry)
    elif args.command == "remove-source":
        registry = load_registry(path)
        result = remove_source(registry, args.ats, args.board_token)
        save_registry(path, registry)
    else:
        registry = load_registry(path)
        sources = _selected_sources(registry, args)
        result = pull_sources(sources, filters=_filters_from_args(args), limit=args.limit)
        cards = [card for item in result["results"] for card in (item.get("cards") or [])]
        if args.output:
            result["written"] = write_cards(cards, args.output)
            manifest = args.output / "manifest.json"
            manifest.write_text(json.dumps(
                {**result, "results": [{key: value for key, value in item.items() if key != "cards"}
                                       for item in result["results"]]},
                indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.chmod(manifest, 0o600)
        if args.db:
            connection = application_core.connect(args.db)
            try:
                result["ingested"] = ingest_cards(
                    connection, cards, allow_possible_duplicate=args.allow_possible_duplicate)
            finally:
                connection.close()
        if not args.print_cards:
            for item in result["results"]:
                item.pop("cards", None)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (SourceError, ValueError) as error:
        # A board that moved, a token that never existed, or a filter that cannot be read
        # is an ordinary outcome for a network-facing puller, not a stack trace.
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
