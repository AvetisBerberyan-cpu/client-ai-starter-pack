#!/usr/bin/env python3
"""CLI entry point for the LLM order-intake app and local review queue.

Business rules, model integration, persistence, request processing, dashboard,
and checks live in their own modules; this file only dispatches CLI commands.
"""

import argparse
import json
from http.server import HTTPServer

import config
import dashboard
import pipeline
from checks import replay_saved_model_calls, run_reference_check


def main() -> int:
    parser = argparse.ArgumentParser(
        description="LLM order intake and local review queue"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Run live reference cases and verify duplicate, reprocessing, and "
            "correction behavior"
        ),
    )
    parser.add_argument(
        "--replay-calls",
        action="store_true",
        help=(
            "Revalidate saved real model responses without making model calls"
        ),
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start the local interactive review queue",
    )
    parser.add_argument(
        "--ask",
        nargs="?",
        const="",
        metavar="TEXT",
        help="Process one order message; prompt for the text if omitted",
    )
    parser.add_argument(
        "--order-ref", help="Optional order reference to use with --ask"
    )
    parser.add_argument(
        "--review",
        metavar="REQUEST_ID",
        help="Correct a saved draft identified by its request ID",
    )
    parser.add_argument(
        "--field",
        choices=["sku", "quantity"],
        help="Draft field to change with --review",
    )
    parser.add_argument(
        "--value",
        help="Replacement SKU or positive whole-number quantity for --review",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address for --serve (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port number for --serve (default: 8765)",
    )
    args = parser.parse_args()

    if args.ask is not None:
        text = args.ask.strip() or input("Enter the order message: ").strip()
        if not text:
            parser.error("Order text cannot be empty.")
        request = pipeline.make_interactive_request(text, args.order_ref)
        rows = pipeline.process_requests([request], config.CATALOG)
        answer = next(row for row in rows if row["id"] == request["id"])
        print(json.dumps(answer, indent=2, ensure_ascii=False))
        return 0 if answer["status"] != "failed" else 1

    if args.review:
        if not args.field or args.value is None:
            parser.error("--review requires --field and --value")
        result = pipeline.review_correction(
            args.review, args.field, args.value, config.CATALOG
        )
        print(json.dumps(result, indent=2))
        return 0

    if args.check:
        report = run_reference_check()
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1

    if args.replay_calls:
        replayed = replay_saved_model_calls()
        print(json.dumps(replayed, indent=2, ensure_ascii=False))
        return 0

    if args.serve:
        print(
            f"Review queue: http://{args.host}:{args.port}/  (Ctrl+C to stop)"
        )
        HTTPServer((args.host, args.port), dashboard.Handler).serve_forever()
        return 0

    requests = pipeline.all_requests()
    rows = pipeline.process_requests(requests, config.CATALOG)
    print(json.dumps(pipeline.summary(rows), indent=2))
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
