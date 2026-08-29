"""Generates real, minimal, spec-valid one-page PDFs for the five upload test scenarios
(src/web/src/data/testInvoiceScenarios.ts) so there's an actual file to pick in the
"Upload an invoice" file input, not just a name typed into a field.

No PDF library is installed (reportlab/fpdf aren't dependencies of this project — they'd
only ever be needed for this one dev convenience), so this writes PDF syntax directly: a
Catalog, one Page, a Helvetica font, and a content stream of positioned text lines, with
byte-exact xref offsets computed as it goes. It's deliberately minimal, not a general-purpose
PDF writer — five short invoice-shaped documents is the entire job.

Run with:
    .venv/bin/python scripts/generate_sample_invoices.py
"""

from __future__ import annotations

from pathlib import Path

OUT_DIR = Path(__file__).parent.parent / "src/web/public/sample-invoices"


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(lines: list[str], *, start_y: int = 720, leading: int = 22) -> bytes:
    parts = ["BT", "/F1 12 Tf", f"50 {start_y} Td"]
    for i, line in enumerate(lines):
        if i > 0:
            parts.append(f"0 -{leading} Td")
        parts.append(f"({_escape(line)}) Tj")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1")


def build_pdf(title_lines: list[str]) -> bytes:
    content = _content_stream(title_lines)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]  # object 0 is the free-list head, never written directly
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF"
    ).encode()
    return bytes(out)


SCENARIOS: dict[str, list[str]] = {
    "clean-beta-traders-inv-9001": [
        "TAX INVOICE",
        "",
        "Beta Traders",
        "GSTIN: 27AAPFU0939F1ZV",
        "",
        "Invoice Number: INV-9001",
        "Invoice Date: 2026-08-10",
        "Due Date: 2026-09-09",
        "",
        "Subtotal:      INR 2000.00",
        "Tax (18%):     INR  360.00",
        "Total:         INR 2360.00",
        "Amount Due:    INR 2360.00",
    ],
    "mismatch-beta-traders-inv-9002": [
        "TAX INVOICE",
        "",
        "Beta Traders",
        "GSTIN: 27AAPFU0939F1ZV",
        "",
        "Invoice Number: INV-9002",
        "Invoice Date: 2026-08-11",
        "Due Date: 2026-09-10",
        "",
        "Subtotal:      INR 2000.00",
        "Tax (18%):     INR  360.00",
        "Total:         INR 2500.00",  # deliberately wrong — should be 2360.00
        "Amount Due:    INR 2500.00",
    ],
    "new-vendor-gamma-logistics-inv-9003": [
        "TAX INVOICE",
        "",
        "Gamma Logistics",
        "(first invoice on file - no processing history)",
        "",
        "Invoice Number: INV-9003",
        "Invoice Date: 2026-08-12",
        "Due Date: 2026-09-11",
        "",
        "Subtotal:      INR 1300.00",
        "Tax (18%):     INR  234.00",
        "Total:         INR 1534.00",
        "Amount Due:    INR 1534.00",
    ],
    "low-confidence-delta-chemicals-inv-9004": [
        "TAX INVOICE",
        "",
        "Delta Chemicals",
        "",
        "Invoice Number: INV-9004",
        "Invoice Date: 2026-08-13",
        "Due Date: 2026-09-12",
        "",
        "Subtotal:      INR  800.00",
        "Tax (18%):     INR  144.00",
        "Total:         INR  944.00",
        "Amount Due:    INR  944.00",
    ],
    "duplicate-acme-inv-1001": [
        "TAX INVOICE",
        "",
        "Acme Supplies Pvt Ltd",
        "GSTIN: 27AAPFU0939F1ZV",
        "",
        "Invoice Number: INV-1001",
        "Invoice Date: 2026-08-01",
        "Due Date: 2026-08-31",
        "",
        "Subtotal:      INR 1000.00",
        "Tax (18%):     INR  180.00",
        "Total:         INR 1180.00",
        "Amount Due:    INR 1180.00",
        "",
        "(This exact invoice is already on file - upload it to see BR-04 trip.)",
    ],
}


def build_blank_page_pdf() -> bytes:
    """A genuinely blank 612x792pt (8.5x11in) page — no text content at all. Used as
    public/sample-invoice.pdf, which src/web/src/data/mockInvoice.ts's own docstring says
    its bounding-box overlays are positioned against: they land on blank paper by design,
    not real text, so this must stay content-free rather than reuse build_pdf().
    """
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << >> /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF"
    ).encode()
    return bytes(out)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, lines in SCENARIOS.items():
        path = OUT_DIR / f"{name}.pdf"
        path.write_bytes(build_pdf(lines))
        print(f"wrote {path}")

    blank_path = OUT_DIR.parent / "sample-invoice.pdf"
    blank_path.write_bytes(build_blank_page_pdf())
    print(f"wrote {blank_path}")


if __name__ == "__main__":
    main()
