#!/usr/bin/env python3
"""Read-only audit of what a machine actually reads in a registered resume artifact.

Jobloom locks a resume's bytes but has never checked that those bytes still say
anything a parser can read. This audit answers two questions about one artifact,
under one named extractor policy, and changes nothing:

  forward   does every approved claim resolve to a span of the machine view?
  backward  does every block of the machine view resolve to an approved claim?

It proposes. It never confirms: bindings are `proposed`, block classifications are
`unconfirmed`, and only a user may record `human_confirmed` in a separate sidecar.
It writes nothing to the registry, the manifest, or the database.

Two modes, because they carry different guarantees:

  scan            counts and hashes only. `observation_storage: hash_only`, which is
                  explicitly not admissible for approval - a binding confirmed against
                  a hash nobody kept points at an observation nobody can ever read back.
  prepare-review  writes the exact raw and canonical machine views, the diagnostics and
                  the packet into a private directory it creates exclusively at 0700/0600.
                  `observation_storage: observation_preserved`. Only this output may feed
                  a confirmation flow.

Two interface decisions the audit depends on, both deliberate:

* The canonicalizer is defined **character-wise** (NFKC per character, then
  whitespace-run folding) rather than over the whole string. Whole-string NFKC has
  no total inverse, so a canonical offset could not be projected back to a raw
  offset, and every block-level count would be a guess. Character-wise costs a
  small amount of normalization fidelity and buys a total canonical->raw map.
* A binding carries **every** occurrence, not the first. A resume may legitimately
  repeat a capability in the summary, the skills list, and a bullet. Recording one
  and dropping the rest would resurface the others as unmapped substantive spans
  in the backward direction. Multiple occurrences are a location question for the
  user, not an artifact defect.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

AUDIT_VERSION = "artifact-integrity-audit-v1"
CANONICALIZER_VERSION = "jobloom-machine-view-v1"
EXTRACTION_MODE = "layout"
POLICY_PREFIX = "jobloom-pypdf"
CANDIDATE_TOKEN_THRESHOLD = 0.6
POLICY_FORMATS = {"pdf"}

WHITESPACE = re.compile(r"\s")

# Known extractor messages get a stable internal code so the terminal can name them
# without echoing extractor text, which may carry paths or document metadata.
DIAGNOSTIC_CODES = (
    (re.compile(r"^Ignoring wrong pointing object"), "pypdf.wrong_pointing_object"),
    (re.compile(r"^Multiple definitions in dictionary"), "pypdf.duplicate_dict_key"),
    (re.compile(r"^incorrect startxref"), "pypdf.bad_startxref"),
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


# --------------------------------------------------------------------------- canonicalization

def canonicalize(raw: str) -> tuple[str, list[int]]:
    """Return (canonical_text, canonical_to_raw) with one raw index per canonical char.

    Character-wise NFKC, then whitespace runs folded to a single U+0020 anchored at
    the first raw character of the run. Leading and trailing whitespace is dropped.
    The returned map is total: every canonical index projects to a raw index.
    """
    chars: list[str] = []
    origin: list[int] = []
    in_space = False
    for index, char in enumerate(raw):
        if WHITESPACE.match(char):
            if not in_space:
                chars.append(" ")
                origin.append(index)
                in_space = True
            continue
        in_space = False
        for expanded in unicodedata.normalize("NFKC", char):
            chars.append(expanded)
            origin.append(index)
    start, end = 0, len(chars)
    while start < end and chars[start] == " ":
        start += 1
    while end > start and chars[end - 1] == " ":
        end -= 1
    return "".join(chars[start:end]), origin[start:end]


def project(canonical_start: int, canonical_end: int, canonical_to_raw: list[int]) -> tuple[int, int]:
    """Project a canonical [start, end) span onto raw character offsets."""
    if canonical_start >= canonical_end:
        raise ValueError("empty canonical span")
    return canonical_to_raw[canonical_start], canonical_to_raw[canonical_end - 1] + 1


def occurrences(haystack: str, needle: str) -> list[tuple[int, int]]:
    """Every non-overlapping occurrence of needle in haystack, left to right."""
    if not needle:
        return []
    found: list[tuple[int, int]] = []
    cursor = 0
    while True:
        index = haystack.find(needle, cursor)
        if index < 0:
            return found
        found.append((index, index + len(needle)))
        cursor = index + len(needle)


def count_overlaps(spans: list[tuple[int, int, str]]) -> int:
    """Pairs of spans from different claims that overlap, nesting included.

    Comparing sorted neighbours only would miss A=[0,100] against C=[30,40] whenever a
    disjoint B=[10,20] sits between them in sort order, and report a green zero. At this
    scale the exhaustive pairwise check is cheap and leaves nothing to reason about.
    """
    total = 0
    for index, (start, end, claim) in enumerate(spans):
        for other_start, other_end, other_claim in spans[index + 1:]:
            if claim != other_claim and start < other_end and other_start < end:
                total += 1
    return total


# --------------------------------------------------------------------------- machine view

def raw_blocks(pages: list[str]) -> tuple[str, list[dict]]:
    """Join pages into one raw view and index its non-empty lines as blocks."""
    blocks: list[dict] = []
    parts: list[str] = []
    offset = 0
    for page_number, page in enumerate(pages, start=1):
        for line in page.splitlines(keepends=True):
            stripped = line.strip()
            if stripped:
                lead = len(line) - len(line.lstrip())
                blocks.append({
                    "block_index": len(blocks),
                    "page": page_number,
                    "raw_start": offset + lead,
                    "raw_end": offset + lead + len(stripped),
                })
            offset += len(line)
            parts.append(line)
        parts.append("\n")
        offset += 1
    return "".join(parts), blocks


def blocks_for_span(raw_start: int, raw_end: int, blocks: list[dict]) -> list[int]:
    return [b["block_index"] for b in blocks if b["raw_start"] < raw_end and raw_start < b["raw_end"]]


def extract_pages(pdf_path: Path) -> tuple[list[str], list[str]]:
    """Extract page text under the named policy, capturing extractor diagnostics."""
    from pypdf import PdfReader

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setLevel(logging.DEBUG)
    logger = logging.getLogger("pypdf")
    previous_level, previous_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        reader = PdfReader(str(pdf_path))
        pages = [page.extract_text(extraction_mode=EXTRACTION_MODE) or "" for page in reader.pages]
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate
    diagnostics = [line.strip() for line in buffer.getvalue().splitlines() if line.strip()]
    return pages, diagnostics


def normalize_diagnostic(line: str) -> str:
    return re.sub(r"\d+", "N", line)


def diagnostic_code(line: str) -> str:
    """A stable code that never carries extractor text off the private packet."""
    normalized = normalize_diagnostic(line)
    for pattern, code in DIAGNOSTIC_CODES:
        if pattern.match(normalized):
            return code
    return f"unknown:{sha256_text(normalized)[:12]}"


# --------------------------------------------------------------------------- execution record

def execution_record() -> dict:
    import pypdf

    version = getattr(pypdf, "__version__", "unknown")
    record_hash = None
    try:
        from importlib.metadata import distribution

        dist = distribution("pypdf")
        for file in dist.files or []:
            if file.name == "RECORD":
                record_hash = sha256_bytes(Path(dist.locate_file(file)).read_bytes())
                break
    except Exception:  # noqa: BLE001 - absence is reported, never fatal
        record_hash = None
    return {
        "audit_version": AUDIT_VERSION,
        "extractor_name": "pypdf",
        "extractor_version": version,
        "extractor_distribution_record_sha256": record_hash,
        "extraction_mode": EXTRACTION_MODE,
        "canonicalizer_version": CANONICALIZER_VERSION,
        "extractor_policy_id": f"{POLICY_PREFIX}-{version}-{EXTRACTION_MODE}-policy-v1",
        "python_version": sys.version.split()[0],
        "byte_reproducibility": "observation_only",
    }


# --------------------------------------------------------------------------- audit

GARBAGE = ((re.compile(r"\(cid:\d+\)"), "cid_marker"),
           (re.compile("\ufffd"), "replacement_character"))

# Absence of a locked value can mean the artifact dropped it, or that the fact is
# written in a different surface form than the page renders. Only the two categories a
# parser must reach to contact the candidate at all are treated as a failure; the rest
# are sent to review rather than guessed at.
MUST_BE_LITERAL = {"identity", "contact"}
USABLE_FACT_STATUS = {"locked", "confirmed"}
REVIEW_IF_ABSENT = {"education", "certification", "experience_header"}


def active_candidate_facts(db_path: Path | None) -> list[dict]:
    """Locked and contact facts from the active registered snapshot. Read-only."""
    if not db_path or not db_path.is_file():
        return []
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT snapshot_path FROM candidate_snapshots WHERE status='active' "
            "AND registered_by='user'").fetchone()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    if not row or not Path(row["snapshot_path"]).is_file():
        return []
    return json.loads(Path(row["snapshot_path"]).read_text(encoding="utf-8")).get("facts", [])


def strip_separators(value: str) -> str:
    """Everything that is not a letter or digit, removed. For telling a render difference
    from an absence - never for deciding that a claim is present."""
    return re.sub(r"[^0-9A-Za-z]", "", value).casefold()


def readability_checks(canonical: str, pages: list[str], facts: list[dict]) -> list[dict]:
    """Per-check verdicts on what a parser can reach. Never aggregated into a score.

    A resume can pass every claim binding and still be unreachable: the stock failure is
    a contact line carried by an icon font, which renders perfectly and extracts as glyph
    names. That is why identity and contact are the only categories whose absence is a
    failure rather than a review item.
    """
    checks = [
        {"check": "text_layer", "status": "pass" if canonical.strip() else "fail",
         "detail": {"canonical_chars": len(canonical)}},
        {"check": "page_count", "status": "pass" if pages else "fail",
         "detail": {"pages": len(pages), "pages_with_text": sum(1 for p in pages if p.strip())}},
    ]
    hits = {name: len(pattern.findall(canonical)) for pattern, name in GARBAGE}
    checks.append({"check": "garbage_glyphs",
                   "status": "fail" if any(hits.values()) else "pass", "detail": hits})

    stripped = strip_separators(canonical)
    for category, absent_status in (*((c, "fail") for c in sorted(MUST_BE_LITERAL)),
                                    *((c, "review") for c in sorted(REVIEW_IF_ABSENT))):
        relevant = [f for f in facts
                    if f.get("type") == category
                    and (f.get("locked") or category in MUST_BE_LITERAL)
                    and f.get("status") in USABLE_FACT_STATUS]
        if not relevant:
            continue
        # A value missing literally but present once separators are ignored is a render
        # difference, not something a parser cannot reach. Collapsing the two would make
        # every punctuation choice look like an unreadable contact line, and then the one
        # genuinely unreachable value would be indistinguishable from the noise.
        absent, rendered = [], []
        for fact in relevant:
            value = str(fact.get("value", ""))
            if canonicalize(value)[0] in canonical:
                continue
            (rendered if strip_separators(value) in stripped else absent).append(fact["id"])
        status = absent_status if absent else "review" if rendered else "pass"
        checks.append({
            "check": f"{category}_survives_extraction",
            "status": status,
            "detail": {"checked": len(relevant), "absent": len(absent),
                       "render_difference": len(rendered), "absent_fact_ids": absent,
                       "render_difference_fact_ids": rendered,
                       "locked": all(f.get("locked") for f in relevant)},
        })

    # Reading order needs a human looking at the rendered page beside the machine view.
    # A tool that guessed here would be the single-green-light this whole audit exists
    # to avoid, so it stays a review item no matter how clean the extraction looks.
    checks.append({"check": "reading_order", "status": "user_review", "detail": {}})
    return checks


def tokens(value: str) -> set[str]:
    return set(re.findall(r"\w+", value.casefold()))


def audit_artifact(pdf_path: Path, claims: list[dict], include_spans: bool = False,
                   facts: list[dict] | None = None) -> dict:
    pages, diagnostic_lines = extract_pages(pdf_path)
    raw, blocks = raw_blocks(pages)
    canonical, canonical_to_raw = canonicalize(raw)

    bindings: list[dict] = []
    unresolved: list[dict] = []
    bound_blocks: set[int] = set()
    spans: list[tuple[int, int, str]] = []

    for claim in claims:
        needle, _ = canonicalize(claim["claim_text"])
        hits = occurrences(canonical, needle)
        if not hits:
            unresolved.append(claim)
            continue
        records = []
        for position, (start, end) in enumerate(hits):
            raw_start, raw_end = project(start, end, canonical_to_raw)
            touched = blocks_for_span(raw_start, raw_end, blocks)
            bound_blocks.update(touched)
            spans.append((start, end, claim["claim_id"]))
            records.append({
                "role": "primary" if position == 0 else "repeated",
                "page": blocks[touched[0]]["page"] if touched else None,
                "canonical_start": start, "canonical_end": end,
                "raw_start": raw_start, "raw_end": raw_end,
                "block_indexes": touched,
                "anchor_sha256": sha256_text(canonical[start:end]),
                **({"span_text": canonical[start:end]} if include_spans else {}),
            })
        bindings.append({
            "claim_id": claim["claim_id"],
            "evidence_strength": claim.get("evidence_strength"),
            "binding_basis": "proposed",
            "occurrence_count": len(records),
            "review_required": len(records) > 1,
            "occurrences": records,
        })

    candidates: list[dict] = []
    for claim in unresolved:
        wanted = tokens(claim["claim_text"])
        scored = [
            (len(wanted & tokens(raw[b["raw_start"]:b["raw_end"]])) / max(len(wanted), 1), b["block_index"])
            for b in blocks
        ]
        proposed = sorted((s for s in scored if s[0] >= CANDIDATE_TOKEN_THRESHOLD), reverse=True)
        candidates.append({
            "claim_id": claim["claim_id"],
            "evidence_strength": claim.get("evidence_strength"),
            "candidate_block_indexes": [index for _, index in proposed],
            "binding_basis": "proposed",
            "auto_accepted": False,
        })

    inventory = []
    for block in blocks:
        entry = dict(block)
        entry["intersects_proposed_claim_span"] = block["block_index"] in bound_blocks
        entry["classification"] = "pending_user_confirmation"
        entry["classification_basis"] = "unconfirmed"
        if include_spans:
            entry["text"] = raw[block["raw_start"]:block["raw_end"]]
        inventory.append(entry)

    codes = collections.Counter(diagnostic_code(line) for line in diagnostic_lines)
    return {
        "extraction_status": "ok",
        "readability": readability_checks(canonical, pages, facts or []),
        "machine_view": {
            "raw_sha256": sha256_text(raw),
            "canonical_sha256": sha256_text(canonical),
            "raw_chars": len(raw), "canonical_chars": len(canonical),
            "pages": len(pages), "blocks": len(blocks),
            "offset_map_total": len(canonical_to_raw) == len(canonical),
        },
        "diagnostics": {
            "line_count": len(diagnostic_lines),
            "raw_sha256": sha256_text("\n".join(diagnostic_lines)),
            "codes": [{"code": code, "count": count, "status": "recorded_review"}
                      for code, count in codes.most_common()],
        },
        "claims": {
            "total": len(claims),
            "single_occurrence": sum(1 for b in bindings if b["occurrence_count"] == 1),
            "multiple_occurrences_requiring_review": sum(1 for b in bindings if b["occurrence_count"] > 1),
            "unresolved": len(unresolved),
            "unresolved_by_evidence_strength":
                dict(collections.Counter(c.get("evidence_strength") for c in unresolved)),
            "span_collision_overlap": count_overlaps(spans),
        },
        "blocks": {
            "total": len(blocks),
            "intersecting_proposed_claim_spans": len(bound_blocks),
            "not_intersecting": len(blocks) - len(bound_blocks),
            "classification": "pending_user_confirmation",
        },
        "bindings": bindings,
        "unresolved_candidates": candidates,
        "block_inventory": inventory,
        "_views": {"raw": raw, "canonical": canonical, "diagnostics": diagnostic_lines},
    }


FAILURE_CODES = {
    "FileNotDecryptedError": "encrypted_pdf",
    "EmptyFileError": "empty_file",
    "PdfReadError": "unreadable_pdf",
    "PdfStreamError": "truncated_pdf",
    "JSONDecodeError": "unreadable_manifest",
    "KeyError": "manifest_missing_field",
}


def failure_code(error: Exception) -> str:
    return FAILURE_CODES.get(type(error).__name__, "extraction_failed")


# --------------------------------------------------------------------------- registry

def registry_roles(db_path: Path | None) -> dict[str, dict]:
    """Read version roles from the registry, read-only. Absence is reported, never assumed."""
    if not db_path or not db_path.is_file():
        return {}
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return {
            row["version_id"]: {"kind": row["kind"], "source_mode": row["source_mode"],
                                "status": row["status"]}
            for row in connection.execute(
                "SELECT version_id, kind, source_mode, status FROM resume_versions")
        }
    except sqlite3.Error:
        return {}
    finally:
        connection.close()


def submission_path_reachable_by_role(role: dict | None) -> bool | None:
    """A coarse status/kind screen, deliberately weaker than what bind_version does.

    `bind_version` additionally requires an active registered CandidateSnapshot, a live
    direction whose profile hash still matches, an approved adaptation or baseline plan,
    and a matching file hash. None of that is simulated here, so this must never be read
    as "bind would accept this today" - only as "nothing about its status or kind rules
    it out, and no format or integrity gate stands in the way."
    """
    if role is None:
        return None
    return role["status"] == "approved" and role["kind"] != "master_source"


def integrity_status(role: dict | None, artifact_format: str | None, has_manifest: bool) -> str:
    """Fail closed: no branch here returns a benign terminal state on missing evidence."""
    if artifact_format is None:
        return "no_artifact"
    if not has_manifest:
        return "no_manifest"
    if role is None:
        return "pending_user_review"
    if role["kind"] == "master_source":
        return "not_submission_artifact"
    if artifact_format not in POLICY_FORMATS:
        return "unsupported_format"
    return "pending_user_review"


def scan_registry(store: Path, db_path: Path | None = None) -> dict:
    """Metadata-only scan of every registered version. Never reads a resume body."""
    roles = registry_roles(db_path)
    rows = []
    for directory in sorted(p for p in store.iterdir() if p.is_dir()):
        files = {p.suffix.lower().lstrip("."): p for p in directory.iterdir() if p.is_file()}
        artifact = next((files[ext] for ext in ("pdf", "docx", "txt", "md") if ext in files), None)
        artifact_format = artifact.suffix.lower().lstrip(".") if artifact else None
        has_manifest = (directory / "claims-manifest.json").exists()
        role = roles.get(directory.name)
        status = integrity_status(role, artifact_format, has_manifest)
        reachable = submission_path_reachable_by_role(role)
        rows.append({
            "version_dir": directory.name,
            "kind": role["kind"] if role else None,
            "source_mode": role["source_mode"] if role else None,
            "registry_status": role["status"] if role else None,
            "artifact_format": artifact_format,
            "artifact_sha256": sha256_bytes(artifact.read_bytes()) if artifact else None,
            "has_claims_manifest": has_manifest,
            "integrity_status": status,
            "submission_path_reachable_by_role": reachable,
            # Passed the coarse status/kind screen and carries no approved integrity
            # evidence, with no format gate in the way. `unsupported_format` is not a
            # pass; this is what keeps that visible.
            "format_exposure_candidate": bool(reachable) and status != "approved",
        })
    return {
        "versions": rows,
        "format_exposure_candidates": sum(1 for row in rows if row["format_exposure_candidate"]),
    }


SCOPE = {
    "readability": "scoped to the named extractor policy; identical parsing by any "
                   "employer ATS is not tested and not claimed",
    "claim_to_fact": "inherited_human_confirmation; this audit does not re-prove that the "
                     "user-selected CandidateFact is the semantically correct source",
    "claim_to_span": "proposed; no binding here is user-confirmed",
    "block_classification": "unconfirmed; no block is classified by this tool",
    "readability_checks": "reported per check and never aggregated; reading order is always a user review item",
    "byte_reproducibility": "observation_only",
    "general_ats_compatibility": "not_tested",
}

# Facts about other modules that this audit assumes rather than proves. A canary test
# fails when one stops holding, so a fixed gate cannot leave a stale claim behind here.
AUDIT_ASSUMPTIONS = {
    "format_gate_absent_in_bind_and_lock":
        "resume_core.bind_version and resume_core.lock_materials check approval, "
        "authorization and file hash, but not artifact format; fill_core uploads the "
        "locked snapshot_path as registered. Read from resume_core by hand at "
        f"{AUDIT_VERSION}. A test watches those two bodies for an inline format check, "
        "but it is a drift reminder, not proof: a gate added through a helper such as "
        "_require_submission_artifact would pass it. Settling this needs an integration "
        "fixture that binds a DOCX version, which belongs with the gate itself.",
}


# --------------------------------------------------------------------------- private output

def write_private(path: Path, content: str) -> None:
    """Create exclusively at 0600. Never overwrite: a review packet is an observation."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def write_shared_observation(path: Path, content: str) -> dict:
    """Write a machine view, or accept an identical one already there.

    A MachineView is addressed by PDF hash and extractor policy, so two reports over the
    same PDF with different manifests legitimately share it. Re-writing is refused; an
    existing file whose bytes differ under an address that claims they cannot is a real
    error and is raised rather than reconciled.
    """
    if path.exists():
        if sha256_text(path.read_text(encoding="utf-8")) != sha256_text(content):
            raise ValueError(f"content-addressed observation differs from stored bytes: {path}")
    else:
        write_private(path, content)
    return {"sha256": sha256_text(content), "size": len(content.encode("utf-8"))}


VIEW_FILENAMES = {"raw": "raw-machine-view.txt", "canonical": "canonical-machine-view.txt",
                  "diagnostics": "diagnostics.txt"}


def write_review_packet(output_dir: Path, packet: dict) -> None:
    """Lay the packet out by content address, never by directory convention.

    views/<pdf-sha>/<policy>/      shared: one observation per PDF per extractor policy
    reports/<pdf-sha>_<manifest-sha>/  not shared: one result per artifact and manifest
    """
    policy = packet["execution"]["extractor_policy_id"]
    output_dir.mkdir(mode=0o700, parents=True)
    (output_dir / "views").mkdir(mode=0o700)
    (output_dir / "reports").mkdir(mode=0o700)

    references = []
    for artifact in packet["artifacts"]:
        views = artifact.pop("_views", {"raw": "", "canonical": "", "diagnostics": []})
        # Each level explicitly: mkdir(parents=True) applies its mode to the leaf only,
        # which left the intermediate <pdf-sha> directory world-readable.
        view_dir = output_dir / "views"
        for part in (artifact["artifact_sha256"], policy):
            view_dir = view_dir / part
            view_dir.mkdir(mode=0o700, exist_ok=True)
        files = {}
        for name, content in (("raw", views["raw"]), ("canonical", views["canonical"]),
                              ("diagnostics", "\n".join(views["diagnostics"]))):
            path = view_dir / VIEW_FILENAMES[name]
            files[name] = {"path": str(path.relative_to(output_dir)),
                           **write_shared_observation(path, content)}
        artifact["observation_files"] = files

        report_dir = (output_dir / "reports"
                      / f"{artifact['artifact_sha256']}_{artifact['claims_manifest_sha256']}")
        report_dir.mkdir(mode=0o700)
        write_private(report_dir / "audit-result.json",
                      json.dumps(artifact, indent=2, ensure_ascii=False))
        references.append({
            "artifact_sha256": artifact["artifact_sha256"],
            "claims_manifest_sha256": artifact["claims_manifest_sha256"],
            "report_path": str((report_dir / "audit-result.json").relative_to(output_dir)),
            "extraction_status": artifact["extraction_status"],
            "admissible_for_approval": artifact["admissible_for_approval"],
        })

    index = {k: v for k, v in packet.items() if k != "artifacts"}
    index["reports"] = references
    write_private(output_dir / "audit-packet.json",
                  json.dumps(index, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------- driver

def run(store: Path, db_path: Path | None, include_spans: bool, preserve: bool) -> dict:
    # `preserve` decides admissibility because a binding confirmed against a hash
    # nobody kept points at an observation nobody can read back.
    registry = scan_registry(store, db_path)
    facts = active_candidate_facts(db_path)
    artifacts: dict[str, dict] = {}
    for directory in sorted(p for p in store.iterdir() if p.is_dir()):
        pdf, manifest = directory / "resume.pdf", directory / "claims-manifest.json"
        if not (pdf.exists() and manifest.exists()):
            continue
        artifact_sha = sha256_bytes(pdf.read_bytes())
        manifest_sha = sha256_bytes(manifest.read_bytes())
        key = f"{artifact_sha}/{manifest_sha}"
        if key in artifacts:
            artifacts[key]["aliases"].append(directory.name)
            continue
        try:
            claims = json.loads(manifest.read_text(encoding="utf-8"))["claims"]
            result = audit_artifact(pdf, claims, include_spans=include_spans, facts=facts)
        except Exception as error:  # noqa: BLE001 - one bad artifact must not hide the rest
            result = {"extraction_status": "failed", "failure_code": failure_code(error),
                      "_views": {"raw": "", "canonical": "",
                                 "diagnostics": [f"{type(error).__name__}: {error}"]}}
        result.update({"artifact_sha256": artifact_sha, "claims_manifest_sha256": manifest_sha,
                       "aliases": [directory.name],
                       # Approval attaches to one artifact. A packet-level verdict would let
                       # a failed extraction inherit a green light from its neighbours.
                       "admissible_for_approval": preserve and result["extraction_status"] == "ok"})
        artifacts[key] = result

    listed = list(artifacts.values())
    packet = {"execution": {**execution_record(),
                            "observation_storage": "observation_preserved" if preserve else "hash_only",
                            "all_artifacts_admissible":
                                bool(listed) and all(a["admissible_for_approval"] for a in listed)},
              "scope": SCOPE, "audit_assumptions": AUDIT_ASSUMPTIONS,
              "registry": registry, "artifacts": listed}
    return packet


def report(packet: dict) -> None:
    execution, registry = packet["execution"], packet["registry"]
    print(f"policy          : {execution['extractor_policy_id']}")
    print(f"canonicalizer   : {execution['canonicalizer_version']}")
    print(f"reproducibility : {execution['byte_reproducibility']}")
    print(f"observation     : {execution['observation_storage']}   "
          f"all_artifacts_admissible={execution['all_artifacts_admissible']}")
    print(f"versions scanned: {len(registry['versions'])}   "
          f"{dict(collections.Counter(r['integrity_status'] for r in registry['versions']))}")
    print(f"formats         : {dict(collections.Counter(r['artifact_format'] for r in registry['versions']))}")
    print(f"kinds           : {dict(collections.Counter(r['kind'] for r in registry['versions']))}")
    print(f"registry status : {dict(collections.Counter(r['registry_status'] for r in registry['versions']))}")
    print(f"reachable by role: {sum(1 for r in registry['versions'] if r['submission_path_reachable_by_role'])}"
          f"   format_exposure_candidates: {registry['format_exposure_candidates']}")
    for artifact in packet["artifacts"]:
        print(f"\nartifact {artifact['artifact_sha256'][:12]} / manifest "
              f"{artifact['claims_manifest_sha256'][:12]}   aliases={len(artifact['aliases'])}"
              f"   {artifact['extraction_status']}"
              f"   admissible={artifact['admissible_for_approval']}")
        if artifact["extraction_status"] != "ok":
            print(f"  failure_code {artifact['failure_code']}")
            continue
        for check in artifact["readability"]:
            detail = {k: v for k, v in check["detail"].items() if not k.endswith("_fact_ids")}
            print(f"  readability.{check['check']:32} {check['status']:12} {detail}")
        for section in ("machine_view", "claims", "blocks"):
            for key, value in artifact[section].items():
                print(f"  {section}.{key:38} {value}")
        for code in artifact["diagnostics"]["codes"]:
            print(f"  diagnostic [{code['count']:>3}x] {code['status']:16} {code['code']}")
    print("\nscope")
    for key, value in packet["scope"].items():
        print(f"  {key:22} {value}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", type=Path, default=Path(".jobloom/resumes"))
    parser.add_argument("--db", type=Path, default=Path(".jobloom/jobloom.db"),
                        help="read-only; supplies each version's kind, source_mode and status")
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("scan", help="counts and hashes only; not admissible for approval")
    prepare = sub.add_parser("prepare-review",
                             help="preserve the exact machine views in a new private directory")
    prepare.add_argument("--output-dir", required=True, type=Path,
                         help="a new private directory, named so it identifies this run "
                              "rather than just its date, e.g. "
                              ".jobloom/review-packets/artifact-integrity-<utc-stamp>-<id>")
    prepare.add_argument("--include-spans", action="store_true",
                         help="also embed span and block text in the packet JSON")
    args = parser.parse_args()

    preserve = args.mode == "prepare-review"
    packet = run(args.store, args.db, include_spans=preserve and args.include_spans, preserve=preserve)
    if preserve:
        write_review_packet(args.output_dir, packet)
        print(f"review packet   : {args.output_dir} (0700, files 0600, exclusive create)")
    else:
        for artifact in packet["artifacts"]:
            artifact.pop("_views", None)
    report(packet)


if __name__ == "__main__":
    main()
