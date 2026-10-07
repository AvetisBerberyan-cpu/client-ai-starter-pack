import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import domain
import pipeline
import storage


class OrderPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.db_path = base / "orders.db"
        self.calls_path = base / "model_calls.json"
        self.results_path = base / "processed_results.json"
        self.path_patches = [
            patch.object(storage, "DB_PATH", self.db_path),
            patch.object(storage, "CALLS_PATH", self.calls_path),
            patch.object(storage, "RESULTS_PATH", self.results_path),
        ]
        for item in self.path_patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.path_patches):
            item.stop()
        self.temp.cleanup()

    def extraction(self, product_query, quantity, reason=None):
        parsed = {
            "product_query": product_query,
            "quantity": quantity,
            "reason": reason,
        }
        return {
            "parsed": parsed,
            "raw": json.dumps(parsed),
            "model": "mock-model",
        }

    def test_price_rules_and_half_up_rounding(self):
        self.assertEqual(domain.price(2, 2000), (4000, 0))
        self.assertEqual(domain.price(10, 2000), (18000, 2000))
        self.assertEqual(domain.price(10, 5), (45, 5))

    def test_interactive_request_identity_is_stable(self):
        first = pipeline.make_interactive_request("Please send 2 CAB-1")
        again = pipeline.make_interactive_request("Please send 2 CAB-1")
        self.assertEqual(first, again)
        self.assertEqual(first["source_file"], "interactive")

    def test_local_lookup_requires_catalog_evidence(self):
        self.assertEqual(
            domain.local_catalog_lookup("CAB-1", config.CATALOG)[0]["sku"],
            "CAB-1",
        )
        self.assertEqual(
            domain.local_catalog_lookup("Moon adapter", config.CATALOG), []
        )
        self.assertEqual(
            len(domain.local_catalog_lookup("usual cable", config.CATALOG)), 2
        )

    def test_package_word_overrides_model_count_as_ambiguous(self):
        request = {
            "id": "BOX",
            "order_ref": "BOX",
            "text": "Send two boxes of the usual cable.",
        }
        model_answer = self.extraction("usual cable", 2)
        result = domain.evaluate(request, model_answer, config.CATALOG)
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(
            result["reason"], "ambiguous product and box quantity"
        )
        self.assertIn("individual items", result["clarification_draft"])

    def test_normal_unknown_ambiguous_and_duplicate(self):
        requests = [
            {
                "id": "N",
                "order_ref": "O1",
                "text": "2 CAB-1",
                "source_file": "test",
            },
            {
                "id": "U",
                "order_ref": "O2",
                "text": "one Moon adapter",
                "source_file": "test",
            },
            {
                "id": "A",
                "order_ref": "O3",
                "text": "two boxes usual cable",
                "source_file": "test",
            },
            {
                "id": "D",
                "order_ref": "O1",
                "text": "2 CAB-1",
                "source_file": "test",
            },
        ]
        answers = {
            "2 CAB-1": self.extraction("CAB-1", 2),
            "one Moon adapter": self.extraction("Moon adapter", 1),
            "two boxes usual cable": self.extraction(
                "usual cable", None, "ambiguous quantity"
            ),
        }
        with patch.object(
            pipeline,
            "extract_order",
            side_effect=lambda text, _catalog: answers[text],
        ):
            rows = pipeline.process_requests(requests, config.CATALOG)
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id["N"]["draft"]["total_cents"], 4000)
        self.assertEqual(by_id["U"]["reason"], "unknown product")
        self.assertEqual(by_id["A"]["status"], "needs_clarification")
        self.assertEqual(by_id["D"]["status"], "duplicate")
        self.assertTrue(by_id["U"]["clarification_draft"])

    def test_reprocessing_and_correction_persist(self):
        request = {
            "id": "N",
            "order_ref": "O1",
            "text": "2 CAB-1",
            "source_file": "test",
        }
        answer = self.extraction("CAB-1", 2)
        with patch.object(pipeline, "extract_order", return_value=answer):
            pipeline.process_requests([request], config.CATALOG)
            pipeline.process_requests([request], config.CATALOG)
        conn = sqlite3.connect(self.db_path)
        request_count = conn.execute(
            "SELECT COUNT(*) FROM requests"
        ).fetchone()[0]
        self.assertEqual(request_count, 1)
        conn.close()
        changed = pipeline.review_correction(
            "N", "quantity", "12", config.CATALOG
        )
        self.assertEqual(changed["draft"]["total_cents"], 21600)
        conn = sqlite3.connect(self.db_path)
        correction_count = conn.execute(
            "SELECT COUNT(*) FROM corrections"
        ).fetchone()[0]
        self.assertEqual(correction_count, 1)
        saved_row = conn.execute(
            "SELECT payload FROM requests WHERE request_id='N'"
        ).fetchone()
        saved = json.loads(saved_row[0])
        self.assertEqual(saved["draft"]["quantity"], 12)
        reviewed = conn.execute(
            "SELECT reviewed FROM requests WHERE request_id='N'"
        ).fetchone()[0]
        self.assertEqual(reviewed, 1)
        conn.close()

    def test_repeated_identity_in_one_batch_is_processed_once(self):
        request = {
            "id": "N",
            "order_ref": "O1",
            "text": "2 CAB-1",
            "source_file": "test",
        }
        with patch.object(
            pipeline,
            "extract_order",
            return_value=self.extraction("CAB-1", 2),
        ) as model_call:
            rows = pipeline.process_requests(
                [request, request], config.CATALOG
            )
        self.assertEqual(model_call.call_count, 1)
        self.assertEqual(sum(row["id"] == "N" for row in rows), 1)

    def test_model_failure_is_saved_and_does_not_stop_batch(self):
        requests = [
            {
                "id": "E1",
                "order_ref": "E1",
                "text": "first",
                "source_file": "test",
            },
            {
                "id": "E2",
                "order_ref": "E2",
                "text": "second",
                "source_file": "test",
            },
        ]
        with patch.object(
            pipeline,
            "extract_order",
            side_effect=[RuntimeError("offline"), RuntimeError("offline")],
        ):
            rows = pipeline.process_requests(requests, config.CATALOG)
        self.assertEqual(sum(row["status"] == "failed" for row in rows), 2)
        self.assertEqual(len(json.loads(self.calls_path.read_text())), 2)


if __name__ == "__main__":
    unittest.main()
