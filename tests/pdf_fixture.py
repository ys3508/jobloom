"""A minimal one-page PDF whose text layer holds the lines given.

Shared so the audit tests and the binding tests do not import each other: importing
one test module from another made the result depend on which invocation ran.
"""

from __future__ import annotations


def synthetic_pdf(lines: list[str]) -> bytes:
    """A minimal one-page PDF whose text layer holds `lines`, one per rendered row."""
    rows = "\n".join(f"BT /F1 12 Tf 72 {720 - 18 * i} Td ({line}) Tj ET"
                     for i, line in enumerate(lines))
    stream = rows.encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref))
    return bytes(out)
