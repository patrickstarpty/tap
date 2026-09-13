#!/usr/bin/env python3
"""Generate finite inert adversarial envelopes; no executable exploit payloads."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TYPES = b"""<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>"""
RELS = b"""<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>"""
DOCUMENT = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Parser recovery fixture. A retained safe fact.</w:t></w:r></w:p></w:body></w:document>"""


def docx(extra: dict[str, bytes] | None = None) -> bytes:
    entries = {
        "[Content_Types].xml": TYPES,
        "_rels/.rels": RELS,
        "word/document.xml": DOCUMENT,
        **(extra or {}),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return output.getvalue()


def pdf(pages: int = 1, *, encrypted: bool = False) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            "<< /Type /Pages /Count %d /Kids [%s] >>"
            % (pages, " ".join(f"{3 + i} 0 R" for i in range(pages)))
        ).encode(),
    ]
    objects += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>"] * pages
    if encrypted:
        objects.append(
            b"<< /Filter /Standard /V 1 /R 2 /Length 40 /O <"
            + b"00" * 32
            + b"> /U <"
            + b"00" * 32
            + b"> /P -4 >>"
        )
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    start = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    encrypt = (
        f" /Encrypt {len(objects)} 0 R /ID [<0123456789abcdef><0123456789abcdef>]"
        if encrypted
        else ""
    )
    output.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R{encrypt} >>\nstartxref\n{start}\n%%EOF\n".encode()
    )
    return bytes(output)


def build(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    cases = [
        ("valid.docx", DOCX, None, docx()),
        (
            "macro.docx",
            DOCX,
            "invalid-document",
            docx({"word/vbaProject.bin": b"INERT"}),
        ),
        (
            "ole.docx",
            DOCX,
            "invalid-document",
            docx({"word/embeddings/oleObject1.bin": b"INERT"}),
        ),
        (
            "script.docx",
            DOCX,
            "invalid-document",
            docx({"word/activeX/activeX1.xml": b"<inert/>"}),
        ),
        (
            "external.docx",
            DOCX,
            "invalid-document",
            docx(
                {
                    "word/_rels/document.xml.rels": b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" TargetMode="External" Target="http://192.0.2.1/private-secret"/></Relationships>'
                }
            ),
        ),
        ("traversal.docx", DOCX, "invalid-document", docx({"../escape": b"inert"})),
        ("absolute.docx", DOCX, "invalid-document", docx({"C:/escape": b"inert"})),
        (
            "ratio.docx",
            DOCX,
            "document-too-complex",
            docx({"word/repeated.xml": b"a" * 200_000}),
        ),
        (
            "oversize-entry.docx",
            DOCX,
            "document-too-complex",
            docx({"word/repeated.xml": b"a" * (8 * 1024 * 1024 + 1)}),
        ),
        (
            "entries.docx",
            DOCX,
            "document-too-complex",
            docx({f"extra/{i}.xml": b"<x/>" for i in range(2046)}),
        ),
        (
            "entity.docx",
            DOCX,
            "invalid-document",
            docx(
                {
                    "word/document.xml": b'<!DOCTYPE x [<!ENTITY bad SYSTEM "file:///forbidden">]><x>&bad;</x>'
                }
            ),
        ),
        ("pages.pdf", "application/pdf", "document-too-complex", pdf(201)),
        ("encrypted.pdf", "application/pdf", "invalid-document", pdf(encrypted=True)),
        (
            "fake.pdf",
            "application/pdf",
            "invalid-document",
            b"plain text pretending to be PDF",
        ),
        ("fake.txt", "text/plain", "invalid-document", pdf()),
        ("fake.docx", DOCX, "invalid-document", b"%PDF-1.4\n"),
    ]
    manifest = []
    for name, media, expected, content in cases:
        (output / name).write_bytes(content)
        manifest.append(
            {
                "name": name,
                "mediaType": media,
                "expectedError": expected,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    (output / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: build-hostile-document-fixtures.py OUTPUT_DIRECTORY")
    build(Path(sys.argv[1]))
