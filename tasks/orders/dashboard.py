"""Local HTTP review queue and correction-history page."""

import html
import json
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs

from config import CATALOG
from pipeline import review_correction, summary
import storage


def dashboard_page(filter_status: str = "all") -> str:
    rows = storage.save_processed_results()
    shown = [row for row in rows if filter_status == "all" or row["status"] == filter_status]
    stats = summary(rows)
    options = "".join(f'<option value="{status}" {"selected" if filter_status == status else ""}>{status}</option>' for status in ("all", "ready", "needs_clarification", "failed", "duplicate"))
    cards = []
    for row in shown:
        payload = {key: value for key, value in row.items() if key not in {"original_text", "id", "order_ref", "status", "source_file", "reviewed"}}
        detail = f"<pre>{html.escape(json.dumps(payload, indent=2, ensure_ascii=False))}</pre>"
        draft = payload.get("draft")
        correction = ""
        if row["status"] == "ready" and draft:
            correction = f'''<form method="post" action="/review"><input type="hidden" name="request_id" value="{html.escape(row['id'])}">
            <label>Correction <select name="field"><option value="quantity">quantity</option><option value="sku">SKU</option></select></label>
            <input name="value" placeholder="New quantity or SKU" required><button>Save correction & revalidate</button></form>'''
        cards.append(f'''<article><h2>{html.escape(row['id'])} · {html.escape(row['order_ref'])} · {html.escape(row['status'])}{' · reviewed' if row['reviewed'] else ' · not reviewed by a person'}</h2>
        <p><b>Original ({html.escape(row['source_file'])}):</b> {html.escape(row['original_text'])}</p><h3>Proposal, evidence, and findings</h3>{detail}{correction}</article>''')
    return f'''<!doctype html><html><head><meta charset="utf-8"><title>Order operations queue</title><style>
    body{{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem;background:#f5f7fa;color:#182230}}article{{background:white;border:1px solid #d8dee8;border-radius:12px;padding:1rem;margin:1rem 0}}pre{{white-space:pre-wrap;background:#f2f4f7;padding:1rem}}form{{display:flex;gap:.6rem;align-items:center;margin-top:1rem}}button{{padding:.5rem 1rem}}.stats{{font-weight:600}}</style></head><body>
    <h1>Order operations queue</h1><p class="stats">{html.escape(json.dumps(stats, ensure_ascii=False))}</p>
    <form method="get"><label>Filter status <select name="status">{options}</select></label><button>Apply</button></form>{''.join(cards)}</body></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/review-history"):
            conn = storage.ensure_db()
            rows = [dict(row) for row in conn.execute("SELECT * FROM corrections ORDER BY correction_id DESC")]
            conn.close()
            body = "<h1>Correction history</h1><pre>" + html.escape(json.dumps(rows, indent=2)) + "</pre><a href='/'>Back to queue</a>"
        else:
            query = parse_qs(self.path.partition("?")[2])
            body = dashboard_page(query.get("status", ["all"])[0])
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        fields = parse_qs(self.rfile.read(length).decode())
        try:
            review_correction(fields["request_id"][0], fields["field"][0], fields["value"][0], CATALOG)
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
        except Exception as exc:
            data = ("<h1>Correction failed</h1><pre>" + html.escape(str(exc)) + "</pre><a href='/'>Back</a>").encode()
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[http] {fmt % args}")
