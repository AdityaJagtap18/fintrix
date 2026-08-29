import pytest

from core.extraction.splitting import PageRange, SplitValidationError, split_document


def test_single_invoice_document_is_one_trivial_split():
    splits = split_document("doc-1", total_pages=3, boundaries=[PageRange(1, 3)])
    assert len(splits) == 1
    assert splits[0].parent_document_id == "doc-1"
    assert splits[0].split_index == 0
    assert splits[0].page_range.page_count == 3


def test_three_invoice_batch_splits_into_three_traceable_documents():
    # AC-07.1: three independent invoices, each traceable to source file and page range.
    boundaries = [PageRange(1, 2), PageRange(3, 3), PageRange(4, 6)]
    splits = split_document("batch-1", total_pages=6, boundaries=boundaries)
    assert len(splits) == 3
    assert all(s.parent_document_id == "batch-1" for s in splits)
    assert [s.split_index for s in splits] == [0, 1, 2]
    assert [s.page_range for s in splits] == boundaries


def test_boundaries_are_sorted_regardless_of_input_order():
    boundaries = [PageRange(4, 6), PageRange(1, 2), PageRange(3, 3)]
    splits = split_document("batch-1", total_pages=6, boundaries=boundaries)
    assert [s.page_range.start_page for s in splits] == [1, 3, 4]


def test_gap_in_boundaries_is_rejected():
    with pytest.raises(SplitValidationError):
        split_document("doc-1", total_pages=6, boundaries=[PageRange(1, 2), PageRange(4, 6)])


def test_overlap_in_boundaries_is_rejected():
    with pytest.raises(SplitValidationError):
        split_document("doc-1", total_pages=4, boundaries=[PageRange(1, 3), PageRange(3, 4)])


def test_boundaries_not_covering_full_document_are_rejected():
    with pytest.raises(SplitValidationError):
        split_document("doc-1", total_pages=10, boundaries=[PageRange(1, 5)])


def test_empty_boundaries_rejected():
    with pytest.raises(SplitValidationError):
        split_document("doc-1", total_pages=3, boundaries=[])


def test_page_range_rejects_zero_start_page():
    with pytest.raises(ValueError):
        PageRange(0, 2)


def test_page_range_rejects_end_before_start():
    with pytest.raises(ValueError):
        PageRange(5, 2)
