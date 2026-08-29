"""Vendor master resolution (BR-11, BR-12): exact tax-id match first, RapidFuzz name match
as fallback. This is the gate everything else sits behind — an invoice that doesn't resolve
to a vendor can never go straight through (AC-01.3), so getting this right matters more than
almost anything else in the rule catalogue.
"""

from __future__ import annotations

from rapidfuzz import fuzz

from core.models import Vendor, VendorResolution
from core.persistence import VendorLookup
from core.validation.normalise import clean_ocr_text

DEFAULT_NAME_MATCH_THRESHOLD = 88.0


def _normalise_for_match(name: str) -> str:
    return clean_ocr_text(name).lower()


def resolve_vendor(
    extracted_name: str | None,
    extracted_tax_id: str | None,
    vendor_lookup: VendorLookup,
    *,
    name_match_threshold: float = DEFAULT_NAME_MATCH_THRESHOLD,
) -> VendorResolution:
    """Resolve an extracted vendor name/tax-id to the vendor master.

    Tax-id match is authoritative when present — a tax ID is exact-or-nothing, unlike a
    name that OCR can mangle. Falling back to fuzzy name matching only happens when no tax
    ID was extracted or it didn't resolve.
    """
    if extracted_tax_id:
        vendor = vendor_lookup.find_by_tax_id(extracted_tax_id.strip())
        if vendor is not None:
            return VendorResolution(vendor=vendor, method="exact_tax_id", score=100.0)

    if not extracted_name:
        return VendorResolution(vendor=None, method="none", score=0.0)

    target = _normalise_for_match(extracted_name)
    best_vendor: Vendor | None = None
    best_score = 0.0
    for candidate in vendor_lookup.active_vendors():
        score = fuzz.token_sort_ratio(target, _normalise_for_match(candidate.normalised_name))
        if score > best_score:
            best_score = score
            best_vendor = candidate

    if best_vendor is not None and best_score >= name_match_threshold:
        return VendorResolution(vendor=best_vendor, method="fuzzy_name", score=best_score)

    # No confident match — report the best score seen (0.0 if the vendor pool was empty)
    # so BR-12 can distinguish "close but not close enough" from "nothing like it at all".
    return VendorResolution(vendor=None, method="none", score=best_score)
