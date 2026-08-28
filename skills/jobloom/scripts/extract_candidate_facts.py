#!/usr/bin/env python3
"""Create an auditable proposed-fact review packet from a master resume."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree


SECTION_TYPES = {
    "experience": "experience_claim",
    "work experience": "experience_claim",
    "professional experience": "experience_claim",
    "education": "education",
    "skills": "skill",
    "technical skills": "skill",
    "projects": "project",
    "certifications": "certification",
    "licenses": "certification",
    "awards": "achievement",
    "publications": "publication",
}
LOCK_ON_CONFIRM = {"education", "certification", "identity", "experience_header"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
        if text.strip():
            paragraphs.append(text.strip())
    return "\n".join(paragraphs)


def extract_pdf(path: Path) -> str:
    executable = shutil.which("pdftotext")
    if executable:
        with tempfile.TemporaryDirectory(prefix="jobloom-resume-") as temp_dir:
            output = Path(temp_dir) / "resume.txt"
            subprocess.run([executable, "-layout", str(path), str(output)], check=True, capture_output=True)
            return output.read_text(encoding="utf-8", errors="replace")
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError(
            "PDF ingestion requires either the 'pdftotext' command or the pypdf package"
        ) from error
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError("encrypted PDFs must be unlocked before candidate ingestion")
    pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    text = "\n\f\n".join(pages)
    if not text.strip():
        raise ValueError("PDF contains no extractable text; OCR or a text-based source is required")
    return text


def extract_text(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8")
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".pdf":
        return extract_pdf(path)
    raise ValueError("supported resume formats: .txt, .md, .docx, .pdf")


def normalize_heading(line: str) -> str | None:
    cleaned = re.sub(r"[^a-z ]", "", line.casefold()).strip()
    return cleaned if cleaned in SECTION_TYPES else None


def split_skill_line(line: str) -> list[str]:
    value = line.split(":", 1)[1] if ":" in line else line
    parts = re.split(r"[,;|]", value)
    return [part.strip(" •\t") for part in parts if part.strip(" •\t")]


def build_review_packet(path: Path) -> dict:
    raw = path.read_bytes()
    text = extract_text(path)
    source_hash = sha256_bytes(raw)
    facts = []
    section = "unclassified"
    fact_number = 0

    for line_number, original in enumerate(text.splitlines(), start=1):
        line = re.sub(r"\s+", " ", original).strip(" •\t")
        if not line:
            continue
        heading = normalize_heading(line)
        if heading:
            section = heading
            continue
        fact_type = SECTION_TYPES.get(section, "resume_claim")
        values = split_skill_line(line) if fact_type == "skill" else [line]
        for value in values:
            fact_number += 1
            facts.append({
                "id": f"fact-{fact_number:04d}",
                "type": fact_type,
                "value": value,
                "keywords": [value] if fact_type == "skill" else [],
                "source": {
                    "document_sha256": source_hash,
                    "locator": f"line:{line_number}",
                    "excerpt_sha256": sha256_bytes(original.encode("utf-8")),
                },
                "evidence_strength": "mention_only" if fact_type == "skill" else "direct",
                "status": "proposed",
                "decision": "pending",
                "lock_on_confirm": fact_type in LOCK_ON_CONFIRM,
                "review_note": None,
            })

    return {
        "schema_version": "0.2.0",
        "created_at": date.today().isoformat(),
        "source_document": {
            "filename": path.name,
            "sha256": source_hash,
            "format": path.suffix.casefold().lstrip("."),
        },
        "review_instructions": {
            "allowed_decisions": ["confirmed", "rejected"],
            "required_action": "Review every fact; pending facts block candidate.json generation.",
            "evidence_note": "Skills listed without supporting experience remain mention_only unless the reviewer links direct evidence.",
        },
        "facts": facts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.resume.is_file():
        raise SystemExit(f"resume not found: {args.resume}")
    packet = build_review_packet(args.resume)
    args.output.write_text(json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
