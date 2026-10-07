"""Strict Groq extraction integration; no heuristic model fallback."""

import json
import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from langchain_groq import ChatGroq

from config import MODEL_NAME


class OrderExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_query: str | None
    quantity: int | None = Field(ge=1)
    reason: Literal[
        "unknown product",
        "ambiguous product",
        "ambiguous quantity",
        "missing quantity",
    ] | None


def model_client():
    key = os.getenv("GROQ_API_KEY", "").strip()

    return ChatGroq(
        model=MODEL_NAME,
        temperature=float(os.getenv("GROQ_TEMPERATURE", "0.1")),
        api_key=key,
        max_tokens=int(os.getenv("GROQ_MAX_TOKENS", "300")),
    ).bind(
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "order_extraction",
                "schema": OrderExtraction.model_json_schema(),
                "strict": True,
            },
        }
    )


def extract_order(
    request_text: str, catalog: list[dict[str, Any]]
) -> dict[str, Any]:
    """Extract an item phrase and count; validate the response schema."""
    system = (
        "Extract one order line from the email. Return JSON with exactly "
        "these keys: product_query, quantity, reason. product_query is a "
        "short phrase copied from the requested item, or null if absent. "
        "quantity is a positive integer count of individual items, or null "
        "if absent/unclear. Never infer box or "
        "pack sizes. For unclear product or quantity, reason must be one of "
        "unknown product, ambiguous product, ambiguous quantity, "
        "missing quantity; "
        "otherwise reason is null. Do not choose a SKU. Catalog context: "
        + json.dumps(catalog)
    )
    response = model_client().invoke(
        [("system", system), ("human", request_text)]
    )
    raw = response.content

    if not isinstance(raw, str):
        raise ValueError("Model response content was not text.")

    parsed = OrderExtraction.model_validate(json.loads(raw))
    return {"parsed": parsed.model_dump(), "raw": raw, "model": MODEL_NAME}
