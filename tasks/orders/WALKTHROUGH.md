# Order intake walkthrough (3–5 minutes)

## 1. The goal

This local operations queue turns one email-style message into a proposed order. A language model extracts the product phrase and individual-item count. Local code then matches only the supplied catalog, applies the stated pricing rules, and saves a draft or a clarification for staff. No order is sent to a customer or external system.

## 2. The sample data and rules

The supplied `seed.json` contains the three catalog entries and four starter requests. `requests.json` adds six handwritten fictional examples, for ten requests total. Products, cents, currency, and discount rules remain those in `domain.md`. `reference-cases.json` separately records expected results calculated from those rules; generated application results go to `reference-check-report.json` after `--check` is run.

## 3. One success and the important exception

Run a normal request with `python tasks/orders/main.py --ask "Please send 2 individual CAB-1 cables"`. A ready draft should identify CAB-1, quantity 2, and total 4,000 cents. A ten-unit reference checks the discount: 20,000 cents before discount and 18,000 cents after it.

For `Please send 2 boxes of the usual cable`, the catalog has two cable products and no box sizes. The system leaves the product and item quantity unresolved and drafts a clarification. The model cannot create a catalog item or infer a box conversion.

## 4. Review and persistence

Start `python tasks/orders/main.py --serve` to open the localhost queue. A reviewer can filter by status, compare the original message with catalog evidence and findings, and correct a ready draft's SKU or quantity. The application recalculates the price and stores the change in SQLite with a correction history. A valid draft remains unreviewed until a person makes a correction.

## 5. How the result was checked

The five reference behaviors cover a normal order, the bulk discount, an unknown product, ambiguous package quantity, and a duplicate reference. `--check` also reprocesses a stable request ID and applies a reviewer correction. Use `python tasks/orders/main.py --check` to make the live calls and save the observed report. Expected values are separate from application output and can be checked by hand using the cents in the catalog. Saved real responses can be revalidated without an API call using `python tasks/orders/main.py --replay-calls`.

One observed model response extracted `USB Hubs` and quantity 3 correctly but initially failed the exact-token catalog lookup because the catalog says `USB hub`. The local matcher was corrected to accept that simple plural form. This illustrates why model output still passes through deterministic catalog and pricing checks.

## Limitations and time

This is a text-only, one-order-line prototype. It does not read attachments, send email, or integrate with an ERP. Preparation time was not tracked. Live reference results should be generated with the configured Groq credential before presenting a pass/fail report.
