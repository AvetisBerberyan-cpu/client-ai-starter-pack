"""Strict Groq extraction integration; no heuristic model fallback."""

import json
import os
from typing import Any

from langchain_groq import ChatGroq

from config import MODEL_NAME


def model_client() -> ChatGroq:
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key or key.startswith("REPLACE"):
        raise RuntimeError("GROQ_API_KEY is missing. Set it in tasks/orders/.env or the environment.")
    return ChatGroq(
        model=MODEL_NAME,
        temperature=float(os.getenv("GROQ_TEMPERATURE", "0.1")),
        api_key=key,
        max_tokens=int(os.getenv("GROQ_MAX_TOKENS", "300")),
    ).bind(response_format={"type": "json_object"})


def extract_order(request_text: str, catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract an item phrase and explicit count; validate the response schema."""
    system = (
        "Extract one order line from the email. Return JSON with exactly these keys: "
        "product_query, quantity, reason. product_query is a short phrase copied "
        "from the requested item, or null if absent. quantity is a positive integer "
        "count of individual items, or null if absent/unclear. Never infer box or "
        "pack sizes. For unclear product or quantity, reason must be one of "
        "unknown product, ambiguous product, ambiguous quantity, missing quantity; "
        "otherwise reason is null. Do not choose a SKU. Catalog context: "
        + json.dumps(catalog)
    )
    response = model_client().invoke([("system", system), ("human", request_text)])
    raw = response.content
    if not isinstance(raw, str):
        raise ValueError("Model response content was not text.")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or set(parsed) != {"product_query", "quantity", "reason"}:
        raise ValueError("Model response must be a JSON object with product_query, quantity, and reason.")
    query, quantity, reason = parsed["product_query"], parsed["quantity"], parsed["reason"]
    if query is not None and not isinstance(query, str):
        raise ValueError("product_query must be text or null.")
    if quantity is not None and (not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0):
        raise ValueError("quantity must be a positive integer or null.")
    if reason is not None and reason not in {"unknown product", "ambiguous product", "ambiguous quantity", "missing quantity"}:
        raise ValueError("Model returned an unsupported reason.")
    return {"parsed": parsed, "raw": raw, "model": MODEL_NAME}
