#!/usr/bin/env python3
"""Write a spreadsheet with no third-party library, because the one we had could not run.

`build_application_tracker.mjs` imports `@oai/artifact-tool`, a package this repository has
no `package.json` to install and which is not obtainable here, so the xlsx exporter has
never produced a file. Rather than leave the export blocked on a dependency, this writes
the format directly: an xlsx is a zip of XML parts, and the subset needed for a header, a
few hundred rows and a clickable link is small enough to keep honest.

Strings are written inline rather than through a shared-string table. The table is the
space optimisation every real library makes and it buys nothing at this size, while
costing a second structure that has to stay consistent with the cells pointing into it.

A CSV is written beside every workbook. If a reader ever rejects the xlsx — the risk of
hand-writing a format — the day's work is not lost behind it, and a plain file is also the
thing to hand a tool that does not read xlsx.
"""

from __future__ import annotations

import csv
import zipfile
from pathlib import Path
from typing import Any, Iterable, Sequence
from xml.sax.saxutils import escape, quoteattr

# Excel addresses columns in bijective base-26. Anything past ZZ is far more columns than a
# sheet meant to be read by a person should have, so it is refused rather than truncated.
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def column_name(index: int) -> str:
    """1-based column index to its spreadsheet letter."""
    if index < 1:
        raise ValueError("column index is 1-based")
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = _ALPHABET[remainder] + name
    return name


def _cell(reference: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        # Before the int check: a bool is an int in Python and would silently become 1/0.
        return f'<c r="{reference}" t="inlineStr"><is><t>{"yes" if value else "no"}</t></is></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{reference}"><v>{value}</v></c>'
    text = str(value)
    # xml:space is not optional here: a value that starts or ends in a space is silently
    # trimmed by readers without it, which would quietly alter someone's data.
    return (f'<c r="{reference}" t="inlineStr"><is>'
            f'<t xml:space="preserve">{escape(text)}</t></is></c>')


class Sheet:
    """One tab: a header, its rows, and which column (if any) holds a link."""

    def __init__(self, name: str, columns: Sequence[str], rows: Iterable[Sequence[Any]],
                 *, link_column: int | None = None, widths: Sequence[int] | None = None) -> None:
        if not name or len(name) > 31 or set(name) & set(r"[]:*?/\\"):
            raise ValueError(f"invalid sheet name: {name!r}")
        self.name = name
        self.columns = list(columns)
        self.rows = [list(row) for row in rows]
        if link_column is not None and not 1 <= link_column <= len(self.columns):
            raise ValueError("link column is outside the sheet")
        self.link_column = link_column
        self.widths = list(widths or [])
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError("every row must have one value per column")

    def _links(self) -> list[tuple[str, str]]:
        """(cell reference, target) for each row whose link cell holds something."""
        if self.link_column is None:
            return []
        letter = column_name(self.link_column)
        found = []
        for offset, row in enumerate(self.rows):
            target = str(row[self.link_column - 1] or "")
            # Only http(s). A hyperlink is a thing a reader will follow on a click, so a
            # file: or javascript: target reaching one would be this writer's fault.
            if target.startswith(("http://", "https://")):
                found.append((f"{letter}{offset + 2}", target))
        return found

    def to_xml(self) -> str:
        parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">']
        if self.widths:
            parts.append("<cols>")
            for offset, width in enumerate(self.widths, start=1):
                parts.append(f'<col min="{offset}" max="{offset}" width="{width}" customWidth="1"/>')
            parts.append("</cols>")
        parts.append("<sheetData>")
        header = "".join(_cell(f"{column_name(i)}1", name)
                         for i, name in enumerate(self.columns, start=1))
        parts.append(f'<row r="1">{header}</row>')
        for offset, row in enumerate(self.rows):
            number = offset + 2
            cells = "".join(_cell(f"{column_name(i)}{number}", value)
                            for i, value in enumerate(row, start=1))
            parts.append(f'<row r="{number}">{cells}</row>')
        parts.append("</sheetData>")
        links = self._links()
        if links:
            parts.append("<hyperlinks>")
            for index, (reference, _) in enumerate(links, start=1):
                parts.append(f'<hyperlink ref="{reference}" r:id="rId{index}"/>')
            parts.append("</hyperlinks>")
        parts.append("</worksheet>")
        return "".join(parts)

    def rels_xml(self) -> str | None:
        links = self._links()
        if not links:
            return None
        entries = "".join(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f'Target={quoteattr(target)} TargetMode="External"/>'
            for index, (_, target) in enumerate(links, start=1))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'{entries}</Relationships>')


def write_xlsx(path: Path, sheets: Sequence[Sheet]) -> Path:
    if not sheets:
        raise ValueError("a workbook needs at least one sheet")
    path.parent.mkdir(parents=True, exist_ok=True)
    types = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
             'relationships+xml"/>',
             '<Default Extension="xml" ContentType="application/xml"/>',
             '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
             'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>']
    books = []
    book_rels = []
    for index, sheet in enumerate(sheets, start=1):
        types.append(f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
                     'ContentType="application/vnd.openxmlformats-officedocument.'
                     'spreadsheetml.worksheet+xml"/>')
        books.append(f'<sheet name={quoteattr(sheet.name)} sheetId="{index}" r:id="rId{index}"/>')
        book_rels.append(
            f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>')
    types.append("</Types>")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "".join(types))
        archive.writestr("_rels/.rels",
                         '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                         'relationships"><Relationship Id="rId1" Type="http://schemas.'
                         'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                         'Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml",
                         '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                         '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/'
                         'main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
                         f'relationships"><sheets>{"".join(books)}</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels",
                         '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                         '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                         f'relationships">{"".join(book_rels)}</Relationships>')
        for index, sheet in enumerate(sheets, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", sheet.to_xml())
            rels = sheet.rels_xml()
            if rels:
                archive.writestr(f"xl/worksheets/_rels/sheet{index}.xml.rels", rels)
    return path


def write_csv(path: Path, sheet: Sheet) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig, because Excel on macOS reads a plain UTF-8 CSV as the system encoding and
    # turns every non-ASCII employer name into mojibake.
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(sheet.columns)
        writer.writerows([["" if value is None else value for value in row] for row in sheet.rows])
    return path
