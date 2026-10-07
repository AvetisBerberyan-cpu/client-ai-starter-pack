"""Catalog matching, order validation, clarification, and pricing rules."""

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def canonical(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def price(quantity: int, unit_cents: int) -> tuple[int, int]:
    subtotal = quantity * unit_cents
    discount = 0
    if quantity >= 10:
        discount = int(
            (Decimal(subtotal) * Decimal("0.10")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    return subtotal - discount, discount


def local_catalog_lookup(
    query: str | None, catalog: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Find catalog evidence; model text cannot add catalog entries."""
    if not query or not query.strip():
        return []
    q = canonical(query)
    for item in catalog:
        if canonical(item["sku"]) == q:
            return [item]
    ignored_words = {
        "the",
        "usual",
        "please",
        "send",
        "order",
        "item",
        "individual",
    }
    words = set(q.split()) - ignored_words
    if not words:
        return []
    matches = []
    for item in catalog:
        catalog_text = canonical(item["sku"] + " " + item["name"])
        catalog_words = set(catalog_text.split())
        if all(
            word in catalog_words
            or (word.endswith("s") and word[:-1] in catalog_words)
            for word in words
        ):
            matches.append(item)
    return matches


def clarification(reason: str) -> str:
    messages = {
        "unknown product": (
            "Please identify the requested product using a catalog SKU "
            "or description."
        ),
        "ambiguous product": "Please specify which catalog product you mean.",
        "ambiguous product and box quantity": (
            "Please specify which catalog product you mean and confirm "
            "the number "
            "of individual items; package sizes are not defined."
        ),
        "ambiguous quantity": (
            "Please confirm the number of individual items; package sizes "
            "are not "
            "defined."
        ),
        "missing quantity": (
            "Please confirm the number of individual items requested."
        ),
        "invalid model output": (
            "Please review the request manually; the model response could "
            "not be "
            "validated."
        ),
    }
    return messages.get(
        reason, "Please clarify the product and/or individual item count."
    )


def evaluate(
    request: dict[str, Any],
    extraction: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate extracted fields against local catalog and domain rules."""
    parsed = extraction["parsed"]
    matches = local_catalog_lookup(parsed["product_query"], catalog)
    product = matches[0] if len(matches) == 1 else None
    findings = []
    if not matches:
        reason = "unknown product"
        findings.append(
            "No catalog entry supports the extracted product phrase."
        )
    elif len(matches) > 1:
        reason = "ambiguous product"
        findings.append(
            f"{len(matches)} catalog entries match the extracted product "
            "phrase."
        )
    else:
        reason = None

    quantity = parsed["quantity"]
    declared_reason = parsed["reason"]
    package_pattern = (
        r"\b(?:boxes?|crates?|packs?|cartons?|cases?|bundles?|packages?)\b"
    )
    package_ambiguous = re.search(
        package_pattern, request["text"], re.IGNORECASE
    ) is not None
    if package_ambiguous:
        quantity = None
        declared_reason = "ambiguous quantity"
    if declared_reason in {"unknown product", "ambiguous product"}:
        reason = declared_reason
        product = None
    if declared_reason in {"ambiguous quantity", "missing quantity"}:
        quantity = None
    if quantity is None:
        quantity_reasons = {"ambiguous quantity", "missing quantity"}
        quantity_reason = (
            declared_reason
            if declared_reason in quantity_reasons
            else "missing quantity"
        )
        findings.append(
            "No safe positive whole-number item count was extracted."
        )
        unclear_products = {"unknown product", "ambiguous product"}
        if (
            reason in unclear_products
            and quantity_reason == "ambiguous quantity"
        ):
            reason = "ambiguous product and box quantity"
        else:
            reason = reason or quantity_reason

    if product and quantity is not None:
        total, discount = price(quantity, product["unit_cents"])
        draft = {
            "order_ref": request["order_ref"],
            "sku": product["sku"],
            "product_name": product["name"],
            "quantity": quantity,
            "unit_cents": product["unit_cents"],
            "discount_cents": discount,
            "total_cents": total,
            "currency": "USD",
            "source": "local catalog",
        }
        findings.append(
            "Product is supported by one local catalog entry; quantity is a "
            "positive integer; price was calculated in code."
        )
        return {
            "status": "ready",
            "draft": draft,
            "matches": matches,
            "validation_findings": findings,
            "clarification_draft": None,
        }

    if reason is None:
        reason = (
            "ambiguous quantity" if quantity is None else "unknown product"
        )
    return {
        "status": "needs_clarification",
        "reason": reason,
        "matches": matches,
        "validation_findings": findings,
        "clarification_draft": clarification(reason),
    }
