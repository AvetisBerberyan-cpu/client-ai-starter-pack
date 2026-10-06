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
    parser = argparse.ArgumentParser(description="LLM order intake and local review queue")
    parser.add_argument("--check", action="store_true", help="Run the five reference cases using live model calls")
    parser.add_argument("--replay-calls", action="store_true", help="Revalidate previously saved real model responses; makes no new model calls")
    parser.add_argument("--serve", action="store_true", help="Start the local interactive review queue")
    parser.add_argument("--ask", nargs="?", const="", metavar="TEXT", help="Process one order question; prompt for text if omitted")
    parser.add_argument("--order-ref", help="Optional order reference for --ask")
    parser.add_argument("--review", metavar="REQUEST_ID", help="Correct a saved draft field")
    parser.add_argument("--field", choices=["sku", "quantity"])
    parser.add_argument("--value")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
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
        print(json.dumps(pipeline.review_correction(args.review, args.field, args.value, config.CATALOG), indent=2))
        return 0

    if args.check:
        report = run_reference_check()
        print(json.dumps(report, indent=2))
        return 0 if report["passed"] else 1

    if args.replay_calls:
        print(json.dumps(replay_saved_model_calls(), indent=2, ensure_ascii=False))
        return 0

    if args.serve:
        print(f"Review queue: http://{args.host}:{args.port}/  (Ctrl+C to stop)")
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
