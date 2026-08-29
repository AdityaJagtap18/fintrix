"""FR-011, AC-07.1/07.2: turn a batch of detected invoice boundaries into independently
routable documents, each traceable back to its source file and page range. The boundary
*detection* is an engine concern (Phase 2 adapter); this module only validates and shapes
whatever boundaries it's given, which is what stays true regardless of which engine finds
them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PageRange:
    """1-indexed, inclusive on both ends — matches `BoundingRegion.page` and how page
    numbers are shown to a reviewer."""

    start_page: int
    end_page: int

    def __post_init__(self) -> None:
        if self.start_page < 1:
            raise ValueError("start_page is 1-indexed")
        if self.end_page < self.start_page:
            raise ValueError("end_page must be >= start_page")

    @property
    def page_count(self) -> int:
        return self.end_page - self.start_page + 1


@dataclass(frozen=True, slots=True)
class DocumentSplit:
    parent_document_id: str
    split_index: int
    page_range: PageRange


class SplitValidationError(ValueError):
    """Raised when detected boundaries don't add up to a clean partition of the source
    document — that means the classification engine got the boundaries wrong, and
    proceeding with a bad split silently would misattribute pages to the wrong invoice."""


def split_document(
    parent_document_id: str, total_pages: int, boundaries: list[PageRange]
) -> list[DocumentSplit]:
    """AC-07.1: each resulting split is independently routed and traceable to its source
    file and page range. A single-invoice document is the trivial case of one boundary
    spanning every page."""
    _validate_boundaries(boundaries, total_pages)
    return [
        DocumentSplit(parent_document_id=parent_document_id, split_index=i, page_range=page_range)
        for i, page_range in enumerate(sorted(boundaries, key=lambda pr: pr.start_page))
    ]


def _validate_boundaries(boundaries: list[PageRange], total_pages: int) -> None:
    if not boundaries:
        raise SplitValidationError("at least one page range is required")
    expected_start = 1
    for page_range in sorted(boundaries, key=lambda pr: pr.start_page):
        if page_range.start_page != expected_start:
            raise SplitValidationError(
                f"page ranges are not contiguous: expected page {expected_start} to start "
                f"the next range, got {page_range.start_page}"
            )
        expected_start = page_range.end_page + 1
    covered = expected_start - 1
    if covered != total_pages:
        raise SplitValidationError(
            f"page ranges cover {covered} pages but the document has {total_pages}"
        )
