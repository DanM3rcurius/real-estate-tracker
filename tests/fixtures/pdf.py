"""A tiny, hand-built PDF for tests - because a real exposé is 3-14 MB.

``pypdf`` reads but does not write page content, and the repository does not
carry a PDF-generation dependency just for tests. A minimal PDF with one
Helvetica text object per page is a few hundred bytes and is exactly the
shape ``pypdf.PdfReader.extract_text`` handles: one line per ``Tj``, moved
down with ``T*``. Text is WinAnsi-encoded so umlauts and "€" survive, which
is what German exposé lines need.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

_PAGE_WIDTH = 595
_PAGE_HEIGHT = 842
_FONT_SIZE = 11
_LEADING = 14
_LEFT_MARGIN = 50
_TOP = 780


def _escape(line: str) -> bytes:
    raw = line.encode("cp1252", "replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def make_pdf(pages: Sequence[Sequence[str]]) -> bytes:
    """Build a PDF whose pages carry the given lines of text, in order.

    ``make_pdf([["Kaufpreis: 1 €"], ["Seite 2"]])`` is a two-page document.
    An inner sequence may be empty: that page has no text layer, which is
    how a scanned exposé looks to the reader.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    content_ids: list[int] = []
    for lines in pages:
        if lines:
            body = b" ".join(b"(" + _escape(line) + b") Tj T*" for line in lines)
            stream = (
                b"BT /F1 %d Tf %d TL %d %d Td " % (_FONT_SIZE, _LEADING, _LEFT_MARGIN, _TOP)
                + body
                + b" ET"
            )
        else:
            stream = b""
        content_ids.append(
            add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        )

    pages_id = len(objects) + len(pages) + 1
    page_ids: list[int] = []
    for content_id in content_ids:
        page_ids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                % (pages_id, _PAGE_WIDTH, _PAGE_HEIGHT, font_id, content_id)
            )
        )
    kids = b" ".join(b"%d 0 R" % page_id for page_id in page_ids)
    assert add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids))) == pages_id
    catalog_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref_at = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, catalog_id, xref_at)
    )
    return out.getvalue()
