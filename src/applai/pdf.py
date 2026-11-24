"""A plain text as a clean A4 PDF, in pure Python: no browser nor binary to install.

Helvetica 11 pt, wrapped, on as many pages as needed, uncompressed. Characters outside
Windows-1252, the standard fonts' encoding, become "?".
"""

import textwrap

_WIDTH, _HEIGHT, _MARGIN, _SIZE, _LEADING = 595, 842, 56, 11, 15
_COLUMNS = 85  # characters per line: Helvetica 11 pt averages under 5.7 pt each
_LINES = (_HEIGHT - 2 * _MARGIN) // _LEADING
_NARROW_SPACE, _NO_BREAK_SPACE = chr(0x202F), chr(0xA0)  # French typography's; 1252 has the 2nd


def text_pdf(text: str) -> bytes:
    """The PDF of text, one paragraph per line of text."""
    text = text.replace(_NARROW_SPACE, _NO_BREAK_SPACE)
    lines = [line for paragraph in text.splitlines()
             for line in textwrap.wrap(paragraph, _COLUMNS) or [""]]  # fmt: skip
    pages = [lines[i : i + _LINES] for i in range(0, len(lines), _LINES)] or [[]]
    kids = " ".join(f"{5 + 2 * k} 0 R" for k in range(len(pages)))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    for k, page in enumerate(pages):
        top = f"BT /F1 {_SIZE} Tf {_LEADING} TL {_MARGIN} {_HEIGHT - _MARGIN} Td\n".encode()
        stream = top + b"".join(b"(%s) Tj T*\n" % _escaped(line) for line in page) + b"ET"
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_WIDTH} {_HEIGHT}]"
            f" /Resources << /Font << /F1 3 0 R >> >> /Contents {4 + 2 * k} 0 R >>".encode()
        )
    pdf, offsets = bytearray(b"%PDF-1.4\n"), list[int]()
    for number, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref, size = len(pdf), len(objects) + 1
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % size
    pdf += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref)
    return bytes(pdf)


def _escaped(line: str) -> bytes:
    encoded = line.encode("cp1252", errors="replace")
    return encoded.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
