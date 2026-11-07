import pymupdf
import pytest

from core.extraction.preprocessing import (
    EncryptedPdfError,
    UnreadablePdfError,
    detect_invoice_boundaries,
    optimize_document,
    preprocess_document,
    split_bytes,
)
from core.extraction.splitting import PageRange


def _pdf(*page_texts: str | None) -> bytes:
    doc = pymupdf.open()
    for text in page_texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    return doc.tobytes()


def _pdf_with_full_page_image(*, text: str | None = None) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 100, 100), False)
    pix.set_rect(pix.irect, (200, 200, 200))
    page.insert_image(page.rect, pixmap=pix)  # covers the whole page = scan-like
    if text:
        page.insert_text((72, 72), text)
    return doc.tobytes()


def _encrypted_pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")


_CLEAN_INVOICE_TEXT = "Invoice INV-1001\nBill To: Acme Supplies Pvt Ltd\nTotal: 1180.00"


# --- preprocess_document: #1 blank pages, #6 page-count limit ---


def test_a_page_with_text_is_not_blank():
    report = preprocess_document(_pdf(_CLEAN_INVOICE_TEXT), "clean.pdf")

    assert report.page_count == 1
    assert report.blank_page_numbers == ()
    assert not report.exceeds_page_limit


def test_a_page_with_no_text_and_no_images_is_blank():
    report = preprocess_document(_pdf(_CLEAN_INVOICE_TEXT, None), "with-blank.pdf")

    assert report.blank_page_numbers == (2,)


def test_a_page_with_an_image_but_no_text_is_not_blank():
    report = preprocess_document(_pdf_with_full_page_image(), "scanned.pdf")

    assert report.blank_page_numbers == ()


def test_a_document_over_the_page_limit_is_flagged():
    report = preprocess_document(_pdf(*([_CLEAN_INVOICE_TEXT] * 5)), "long.pdf", max_pages=3)

    assert report.exceeds_page_limit


def test_content_hash_is_stable_for_the_same_bytes():
    same_bytes = _pdf(_CLEAN_INVOICE_TEXT)
    a = preprocess_document(same_bytes, "a.pdf")
    b = preprocess_document(same_bytes, "a.pdf")

    assert a.content_hash == b.content_hash


def test_an_unreadable_file_raises_a_domain_error():
    with pytest.raises(UnreadablePdfError):
        preprocess_document(b"this is not a pdf at all", "garbage.pdf")


def test_an_encrypted_file_raises_a_domain_error():
    with pytest.raises(EncryptedPdfError):
        preprocess_document(_encrypted_pdf(_CLEAN_INVOICE_TEXT), "locked.pdf")


# --- optimize_document: #2 DPI downsampling, #3 grayscale, drop_pages folds in #1 ---


def test_a_born_digital_page_is_left_untouched_not_rasterized():
    # Forcing an already-vector-text page through the raster pipeline makes it bigger,
    # not smaller — the whole point of only rasterizing scan-like pages.
    original = _pdf(_CLEAN_INVOICE_TEXT)
    optimized = optimize_document(original, "clean.pdf")

    doc = pymupdf.open(stream=optimized, filetype="pdf")
    assert "Invoice INV-1001" in doc[0].get_text()  # text layer survived


def test_a_scan_like_page_is_rasterized_and_loses_its_text_layer():
    original = _pdf_with_full_page_image(text="Invoice INV-1001")
    optimized = optimize_document(original, "scan.pdf", dpi=100)

    doc = pymupdf.open(stream=optimized, filetype="pdf")
    assert doc[0].get_text().strip() == ""  # flattened to an image, as expected
    assert len(doc[0].get_images()) == 1


def test_dropped_pages_are_removed_entirely():
    original = _pdf(_CLEAN_INVOICE_TEXT, None, "Invoice INV-1002")
    optimized = optimize_document(original, "with-blank.pdf", drop_pages=frozenset({2}))

    doc = pymupdf.open(stream=optimized, filetype="pdf")
    assert doc.page_count == 2
    assert "INV-1001" in doc[0].get_text()
    assert "INV-1002" in doc[1].get_text()


def test_optimize_document_preserves_page_count_when_nothing_is_dropped():
    original = _pdf(_CLEAN_INVOICE_TEXT, "Invoice INV-1002")
    optimized = optimize_document(original, "two-page.pdf")

    doc = pymupdf.open(stream=optimized, filetype="pdf")
    assert doc.page_count == 2


# --- detect_invoice_boundaries + split_bytes: #5 multi-invoice splitting ---


def test_a_single_invoice_document_is_one_boundary():
    content = _pdf("Invoice Number: INV-100\nDetails...", "continued from page 1...")

    boundaries = detect_invoice_boundaries(content, "single.pdf")

    assert boundaries == [PageRange(start_page=1, end_page=2)]


def test_two_invoices_in_one_file_produce_two_boundaries():
    content = _pdf(
        "Invoice Number: INV-100\nAcme Corp",
        "Invoice Number: INV-100\ncontinued",
        "Invoice Number: INV-200\nBeta Traders",
    )

    boundaries = detect_invoice_boundaries(content, "merged.pdf")

    assert boundaries == [
        PageRange(start_page=1, end_page=2),
        PageRange(start_page=3, end_page=3),
    ]


def test_split_bytes_cuts_out_exactly_the_requested_pages():
    content = _pdf("Invoice Number: INV-100", "Invoice Number: INV-200", "page three")

    piece = split_bytes(content, "merged.pdf", PageRange(start_page=2, end_page=3))

    doc = pymupdf.open(stream=piece, filetype="pdf")
    assert doc.page_count == 2
    assert "INV-200" in doc[0].get_text()
    assert "page three" in doc[1].get_text()
