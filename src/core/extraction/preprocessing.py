"""Pre-extraction preprocessing: the six things actually worth doing to a PDF before it's
billed to Document Intelligence.

1. Blank-page detection (NFR-042/T-045) — structural, no rendering: no text and no images
   on a page means it's blank.
2/3. DPI downsampling + grayscale — an active transformation (optimize_document below),
   not a check. Only applied to pages that are actually scan-like (dominated by one large
   embedded image); a page that's already vector text gets left alone, because forcing it
   through the same raster pipeline makes it *bigger*, not smaller, and destroys a
   perfectly good text layer for nothing.
4. Content-hash cache — the hash is computed here (content_hash on PreprocessReport); the
   actual reuse decision lives in core.extraction.cache.decide_cache_reuse, and the
   lookup is a repository concern (InvoiceRepository.get_by_content_hash), wired into
   api/routers/processing.py's extract_and_process.
5. Multi-invoice splitting — detect_invoice_boundaries below is a text-only heuristic
   standing in for Document Intelligence's own classification (plan.md's "real" answer,
   not wired anywhere in this codebase yet); core.extraction.splitting.split_document
   does the actual partitioning once boundaries are known.
6. Page-count pre-check (exceeds_page_limit on PreprocessReport) — a cheap sanity gate
   before anything is billed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import pymupdf

from core.extraction.splitting import PageRange

DEFAULT_MAX_PAGES = 20
DEFAULT_DPI = 150
# A page is treated as "scan-like" — worth rasterizing down to DEFAULT_DPI — only when a
# single embedded image covers more than this fraction of the page. Below that, whatever
# images are there are decorative (a logo, a stamp), not the page's actual content.
_SCAN_IMAGE_AREA_THRESHOLD = 0.5

_INVOICE_NUMBER_PATTERN = re.compile(
    r"invoice\s*(?:number|no\.?|#)?\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{2,20})",
    re.IGNORECASE,
)


class UnreadablePdfError(ValueError):
    """The file isn't a PDF PyMuPDF can parse at all — corrupt, truncated, or some other
    format entirely."""


class EncryptedPdfError(ValueError):
    """Password-protected — nothing below the file's own structure (page count) can be
    read without a password this pipeline never has. Raised rather than silently treating
    the file as if it had zero pages, which would look identical to a genuinely empty PDF.
    """


def _open(content: bytes, filename: str) -> pymupdf.Document:
    try:
        doc = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:  # PyMuPDF's own exception surface varies by failure mode
        # (FileDataError, EmptyFileError, and others) — every one of them means the same
        # thing to a caller: nothing downstream of this can be trusted, reject the file.
        raise UnreadablePdfError(f"could not read {filename!r} as a PDF: {exc}") from exc
    if doc.is_encrypted:
        raise EncryptedPdfError(f"{filename!r} is password-protected")
    return doc


@dataclass(frozen=True, slots=True)
class PreprocessReport:
    filename: str
    page_count: int
    content_hash: str  # sha256 of the raw bytes — T-044's cache key
    blank_page_numbers: tuple[int, ...]  # 1-indexed
    exceeds_page_limit: bool


def preprocess_document(
    content: bytes, filename: str, *, max_pages: int = DEFAULT_MAX_PAGES
) -> PreprocessReport:
    content_hash = hashlib.sha256(content).hexdigest()
    doc = _open(content, filename)

    blank_page_numbers = tuple(
        index + 1
        for index in range(doc.page_count)
        if not doc[index].get_text().strip() and not doc[index].get_images()
    )

    return PreprocessReport(
        filename=filename,
        page_count=doc.page_count,
        content_hash=content_hash,
        blank_page_numbers=blank_page_numbers,
        exceeds_page_limit=doc.page_count > max_pages,
    )


def _is_scan_like_page(page: pymupdf.Page) -> bool:
    """A page whose content is dominated by one large embedded image, rather than vector
    text — the kind of page DPI downsampling actually helps. Checked by comparing each
    embedded image's placed size against the page area, not the image's raw pixel
    dimensions, since a tiny logo scaled up (or a huge photo scaled down) on the page is
    what actually determines how much rendering it costs, not its stored resolution."""
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return False
    for image in page.get_images(full=True):
        xref = image[0]
        for rect in page.get_image_rects(xref):
            if (rect.width * rect.height) / page_area > _SCAN_IMAGE_AREA_THRESHOLD:
                return True
    return False


def optimize_document(
    content: bytes,
    filename: str,
    *,
    dpi: int = DEFAULT_DPI,
    grayscale: bool = True,
    drop_pages: frozenset[int] = frozenset(),
) -> bytes:
    """The actual transformation (#2/#3, and #1 when drop_pages is populated): blank pages
    a caller already found via preprocess_document are removed entirely; a scan-like page
    gets rasterized down to `dpi` (and desaturated, if grayscale) same as Ghostscript's
    -dPDFSETTINGS=/ebook would; a page that's already vector text with no dominant image
    is copied through untouched — rasterizing it would only make the file bigger and
    throw away a perfectly good text layer, the opposite of the point.
    """
    doc = _open(content, filename)
    out = pymupdf.open()
    matrix = pymupdf.Matrix(dpi / 72, dpi / 72)
    colorspace = pymupdf.csGRAY if grayscale else pymupdf.csRGB

    for index in range(doc.page_count):
        if (index + 1) in drop_pages:
            continue
        page = doc[index]
        if not _is_scan_like_page(page):
            out.insert_pdf(doc, from_page=index, to_page=index)
            continue
        pixmap = page.get_pixmap(matrix=matrix, colorspace=colorspace)
        new_page = out.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, pixmap=pixmap)

    result: bytes = out.tobytes(deflate=True, garbage=4)
    return result


def detect_invoice_boundaries(content: bytes, filename: str) -> list[PageRange]:
    """Text-only heuristic standing in for Document Intelligence's own multi-invoice
    classification (plan.md's actual design, not wired anywhere in this codebase). A page
    whose text contains an invoice-number-shaped token different from the previous one
    seen is treated as the start of a new invoice; pages with no number of their own (a
    continuation page, an attachment) stay part of whatever invoice came before them. This
    is a heuristic, not authoritative — it's meant to be reviewed, not blindly trusted.
    """
    doc = _open(content, filename)
    boundaries: list[PageRange] = []
    current_start = 1
    current_number: str | None = None

    for index in range(doc.page_count):
        page_number = index + 1
        match = _INVOICE_NUMBER_PATTERN.search(doc[index].get_text())
        found_number = match.group(1) if match else None
        if found_number and current_number and found_number != current_number:
            boundaries.append(PageRange(start_page=current_start, end_page=page_number - 1))
            current_start = page_number
        if found_number:
            current_number = found_number

    boundaries.append(PageRange(start_page=current_start, end_page=doc.page_count))
    return boundaries


def count_pages(content: bytes, filename: str) -> int:
    """Best-effort page count for cost/metrics purposes only — not a validity check.
    Content too broken for even this to parse still goes on to the real extractor, which
    rejects it on its own terms; this just falls back to counting it as one page rather
    than blocking anything ahead of that.
    """
    try:
        page_count: int = _open(content, filename).page_count
        return page_count
    except (UnreadablePdfError, EncryptedPdfError):
        return 1


def split_bytes(content: bytes, filename: str, page_range: PageRange) -> bytes:
    """Cuts one PageRange out of content as its own standalone PDF — what actually turns
    detect_invoice_boundaries' suggestions into independently-sendable files."""
    doc = _open(content, filename)
    out = pymupdf.open()
    out.insert_pdf(doc, from_page=page_range.start_page - 1, to_page=page_range.end_page - 1)
    result: bytes = out.tobytes(deflate=True, garbage=4)
    return result
