"""Request loading, model-backed processing, analytics, and reviewer corrections."""

import hashlib
import json
from typing import Any

from config import CATALOG_PATH, MODEL_NAME, REQUESTS_PATH
from domain import clarification, price, evaluate
from llm import extract_order
import storage


def process_requests(requests: list[dict[str, Any]], catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conn = storage.ensure_db()
    batch_refs: dict[str, str] = {}
    seen_request_ids: set[str] = set()

    def save(request: dict[str, Any], status: str, result: dict[str, Any]) -> None:
        timestamp = storage.now()
        conn.execute("""INSERT INTO requests(request_id,order_ref,text,original_text,source_file,status,reviewed,payload,created_at,updated_at)
            VALUES(?,?,?,?,?,?,0,?,?,?) ON CONFLICT(request_id) DO UPDATE SET order_ref=excluded.order_ref,text=excluded.text,
            original_text=excluded.original_text,source_file=excluded.source_file,status=excluded.status,
            payload=excluded.payload,updated_at=excluded.updated_at""",
            (request["id"], request["order_ref"], request["text"], request["text"], request.get("source_file", "seed.json"),
             status, json.dumps(result, ensure_ascii=False), timestamp, timestamp))

    def duplicate(request: dict[str, Any], prior_id: str, explanation: str) -> None:
        result = {"status": "duplicate", "same_order_as": prior_id,
                  "validation_findings": [explanation], "matches": [], "clarification_draft": None}
        save(request, "duplicate", result)

    def log_model_call(request: dict[str, Any], source: str, model: str, **details: Any) -> None:
        storage.append_model_call({
            "timestamp": storage.now(), "request_id": request["id"], "order_ref": request["order_ref"],
            "source_file": request.get("source_file", "seed.json"), "input": request["text"],
            "source": source, "model": model, **details,
        })

    for request in requests:
        rid = request["id"]
        if rid in seen_request_ids:
            continue
        seen_request_ids.add(rid)
        if request.get("input_error"):
            result = {"status": "failed", "reason": "unusable input", "error": request["input_error"], "matches": [],
                      "validation_findings": ["Input schema is invalid; no model call was made."], "clarification_draft": None}
            save(request, "failed", result)
            continue
        prior_in_batch = batch_refs.get(request["order_ref"])
        if prior_in_batch and prior_in_batch != rid:
            duplicate(request, prior_in_batch, "Order reference already appeared in this batch; no second draft created.")
            continue
        stored_order = conn.execute("SELECT request_id FROM requests WHERE order_ref=? AND request_id<>? AND status<>'duplicate' ORDER BY created_at LIMIT 1", (request["order_ref"], rid)).fetchone()
        if stored_order:
            duplicate(request, stored_order["request_id"], "Order reference already exists in the saved queue; no second draft created.")
            continue
        batch_refs[request["order_ref"]] = rid
        try:
            call = extract_order(request["text"], catalog)
            result = evaluate(request, call, catalog)
            log_model_call(request, "live_groq", call["model"], response=call["raw"], validated_result=result)
            status = result["status"]
        except Exception as exc:
            result = {"status": "failed", "reason": "model or response validation error", "error": str(exc), "matches": [],
                      "validation_findings": ["No order was drafted because the model call or its structured response failed."],
                      "clarification_draft": clarification("invalid model output")}
            log_model_call(request, "live_groq_error", MODEL_NAME, error=str(exc))
            status = "failed"
        save(request, status, result)
    conn.commit()
    conn.close()
    return storage.save_processed_results()


def all_requests() -> list[dict[str, Any]]:
    requests = []
    for source, path in (("seed.json", CATALOG_PATH), ("requests.json", REQUESTS_PATH)):
        if source == "requests.json" and not path.exists():
            continue
        try:
            contents = json.loads(path.read_text(encoding="utf-8"))
            records = contents["requests"]
            if not isinstance(records, list):
                raise ValueError("'requests' must be a JSON array")
        except Exception as exc:
            requests.append({"id": f"INVALID-{source}", "order_ref": f"INVALID-{source}", "text": "", "source_file": source, "input_error": str(exc)})
            continue
        for index, record in enumerate(records, start=1):
            valid = isinstance(record, dict) and isinstance(record.get("id"), str) and bool(record["id"].strip()) and isinstance(record.get("order_ref"), str) and bool(record["order_ref"].strip()) and isinstance(record.get("text"), str) and bool(record["text"].strip())
            if valid:
                requests.append({"id": record["id"], "order_ref": record["order_ref"], "text": record["text"], "source_file": source})
            else:
                raw_id = record.get("id") if isinstance(record, dict) else None
                raw_ref = record.get("order_ref") if isinstance(record, dict) else None
                raw_text = record.get("text") if isinstance(record, dict) else None
                requests.append({"id": str(raw_id or f"INVALID-{source}-{index}"), "order_ref": str(raw_ref or f"INVALID-{source}-{index}"), "text": str(raw_text or ""), "source_file": source, "input_error": f"Request record {index} must contain non-empty string id, order_ref, and text."})
    return requests


def make_interactive_request(text: str, order_ref: str | None = None) -> dict[str, str]:
    signature = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12].upper()
    stable_ref = order_ref or f"ASK-{signature}"
    request_id = hashlib.sha256((stable_ref + "::" + text).encode("utf-8")).hexdigest()[:12].upper()
    return {"id": f"ASK-{request_id}", "order_ref": stable_ref, "text": text, "source_file": "interactive"}


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {name: sum(1 for row in rows if row["status"] == name) for name in ("ready", "needs_clarification", "failed", "duplicate")}
    reasons: dict[str, int] = {}
    for row in rows:
        reason = row.get("reason")
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    return {"requests": len(rows), "new_orders": counts["ready"] + counts["needs_clarification"], **counts, "exception_reasons": reasons,
            "practical_improvement": "Ask customers for a catalog SKU and a count of individual items; the supplied examples show that generic product names and package counts trigger clarification."}


def review_correction(request_id: str, field: str, value: str, catalog: list[dict[str, Any]]) -> dict[str, Any]:
    conn = storage.ensure_db()
    row = conn.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Unknown request id {request_id!r}; process the request first.")
    payload = json.loads(row["payload"])
    draft = payload.get("draft")
    if not isinstance(draft, dict):
        conn.close()
        raise ValueError("Only a structurally valid draft can be corrected.")
    if field == "sku":
        product = next((item for item in catalog if item["sku"] == value), None)
        if not product:
            conn.close()
            raise ValueError("SKU is not present in the local catalog.")
        old = draft["sku"]
        draft.update({"sku": product["sku"], "product_name": product["name"], "unit_cents": product["unit_cents"]})
    elif field == "quantity":
        if not value.isdigit() or int(value) <= 0:
            conn.close()
            raise ValueError("Quantity must be a positive whole number.")
        old = str(draft["quantity"])
        draft["quantity"] = int(value)
        product = next(item for item in catalog if item["sku"] == draft["sku"])
    else:
        conn.close()
        raise ValueError("Field must be sku or quantity.")
    total, discount = price(int(draft["quantity"]), int(product["unit_cents"]))
    draft["total_cents"], draft["discount_cents"] = total, discount
    payload["matches"] = [product]
    payload["validation_findings"] = ["Reviewer correction revalidated against the local catalog; quantity is a positive integer; price was recalculated in code."]
    result_status = "ready"
    conn.execute("UPDATE requests SET payload=?, status=?, reviewed=1, updated_at=? WHERE request_id=?", (json.dumps(payload), result_status, storage.now(), request_id))
    conn.execute("INSERT INTO corrections(request_id,field,old_value,new_value,status,changed_at) VALUES(?,?,?,?,?,?)", (request_id, field, old, value, result_status, storage.now()))
    conn.commit()
    conn.close()
    storage.save_processed_results()
    return {"request_id": request_id, "field": field, "old_value": old, "new_value": value, "status": result_status, "draft": draft, "person_reviewed": True}
