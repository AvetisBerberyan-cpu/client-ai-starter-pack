"""Independent live reference checks and explicit replay of saved model responses."""

import json
import tempfile
from pathlib import Path
from typing import Any

import config
import storage
from domain import evaluate
from pipeline import process_requests, review_correction


def run_reference_check() -> dict[str, Any]:
    """Run live reference cases in a temporary DB so they do not pollute the queue."""
    saved_db, saved_results = storage.DB_PATH, storage.RESULTS_PATH
    with tempfile.TemporaryDirectory(prefix="orders-reference-") as tmp:
        storage.DB_PATH = Path(tmp) / "reference.db"
        storage.RESULTS_PATH = Path(tmp) / "results.json"
        try:
            references = json.loads(config.REFERENCES_PATH.read_text(encoding="utf-8"))["cases"]
            observed = process_requests(references, config.CATALOG)
            expected_by_id = {case["id"]: case["expected"] for case in references}
            report = []
            for row in observed:
                if row["id"] not in expected_by_id:
                    continue
                expected = expected_by_id[row["id"]]
                checks = {
                    "status": row["status"] == expected["status"],
                    "sku": expected.get("sku") is None or row.get("draft", {}).get("sku") == expected.get("sku"),
                    "quantity": expected.get("quantity") is None or row.get("draft", {}).get("quantity") == expected.get("quantity"),
                    "total_cents": expected.get("total_cents") is None or row.get("draft", {}).get("total_cents") == expected.get("total_cents"),
                    "reason": expected.get("reason") is None or row.get("reason") == expected.get("reason"),
                }
                report.append({"id": row["id"], "expected": expected, "observed": {"status": row["status"], "draft": row.get("draft"), "reason": row.get("reason")}, "checks": checks, "passed": all(checks.values())})

            normal_case = next(case for case in references if case["id"] == "REF-NORMAL")
            conn = storage.ensure_db()
            count_before = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            conn.close()
            reprocessed = process_requests([normal_case], config.CATALOG)
            conn = storage.ensure_db()
            count_after = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            saved = conn.execute("SELECT status FROM requests WHERE request_id=?", (normal_case["id"],)).fetchone()
            conn.close()
            reprocess_checks = {
                "row_count_unchanged": count_after == count_before,
                "same_request_remains_ready": saved is not None and saved["status"] == "ready",
                "one_saved_result_for_id": sum(row["id"] == normal_case["id"] for row in reprocessed) == 1,
            }
            report.append({
                "id": "IDENTICAL-REPROCESS",
                "expected": {"behavior": "same stable request updates its saved row without adding another"},
                "observed": {"rows_before": count_before, "rows_after": count_after, "status": saved["status"] if saved else None},
                "checks": reprocess_checks,
                "passed": all(reprocess_checks.values()),
            })

            correction = json.loads(config.REFERENCES_PATH.read_text(encoding="utf-8"))["review_correction"]
            expected = correction["expected"]
            try:
                corrected = review_correction(correction["request_id"], correction["field"], correction["value"], config.CATALOG)
                correction_checks = {key: corrected.get(key) == value for key, value in expected.items() if key in corrected}
                correction_checks["quantity"] = corrected["draft"]["quantity"] == expected["quantity"]
                correction_checks["total_cents"] = corrected["draft"]["total_cents"] == expected["total_cents"]
                correction_checks["discount_cents"] = corrected["draft"]["discount_cents"] == expected["discount_cents"]
                correction_result = {"id": "REVIEW-CORRECTION", "expected": expected, "observed": corrected, "checks": correction_checks, "passed": all(correction_checks.values())}
            except Exception as exc:
                correction_result = {"id": "REVIEW-CORRECTION", "expected": expected, "observed": {"status": "blocked", "reason": str(exc)}, "checks": {"proposal_ready_for_correction": False}, "passed": False}
            report.append(correction_result)
        finally:
            storage.DB_PATH, storage.RESULTS_PATH = saved_db, saved_results

    result = {"mode": "live model calls", "passed": all(case["passed"] for case in report), "cases": report, "note": "Expected values were calculated independently from the domain rules."}
    config.REFERENCE_REPORT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def replay_saved_model_calls() -> dict[str, Any]:
    """Revalidate recorded real model responses; never synthesize a response."""
    if not storage.CALLS_PATH.exists():
        raise RuntimeError("No saved model_calls.json exists to replay.")
    records = json.loads(storage.CALLS_PATH.read_text(encoding="utf-8"))
    replayed = []
    for call in records:
        if call.get("source") != "live_groq" or not isinstance(call.get("response"), str):
            continue
        request = {"id": call.get("request_id", "cached"), "order_ref": call.get("order_ref", ""), "text": call.get("input", "")}
        try:
            raw = json.loads(call["response"])
            extraction = {"parsed": raw, "raw": call["response"], "model": call.get("model", "unknown-recorded-model")}
            result = evaluate(request, extraction, config.CATALOG)
            replayed.append({"request_id": request["id"], "status": result["status"], "source": "cached real model response", "model": extraction["model"], "result": result})
        except Exception as exc:
            replayed.append({"request_id": request["id"], "status": "failed", "source": "cached real model response", "error": str(exc)})
    report = {"mode": "replay of saved real model responses; no new model calls made", "replayed_count": len(replayed), "results": replayed}
    config.REPLAY_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report
