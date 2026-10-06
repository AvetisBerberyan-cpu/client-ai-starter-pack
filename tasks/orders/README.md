# Order intake operations queue

This folder contains a small local order-intake app. It reads fictional customer requests, uses a Groq model to extract the requested product phrase and item count, checks products against the local catalog, calculates prices in Python, and saves proposed drafts or clarification requests for a person to review. The implementation is divided into modules by responsibility so the model, business rules, storage, and review UI can be reviewed separately.

## Requirements and setup

- Python 3.10 or later (the implementation was checked with Python 3.13.5).
- A Groq API key for processing new requests. There is no heuristic extraction fallback.
- Network access for live Groq calls.

From the repository root, create a virtual environment and install the task dependencies:

```bash
python3.13 -m venv tasks/orders/.venv
source tasks/orders/.venv/bin/activate
python -m pip install -r tasks/orders/requirements.txt
```

Copy the variable names from `.env.example` to a local `tasks/orders/.env` and set `GROQ_API_KEY`. You can set `GROQ_MODEL`, `GROQ_TEMPERATURE`, and `GROQ_MAX_TOKENS` there too. The defaults are `llama-3.3-70b-versatile`, `0.1`, and `300`. Keep `.env` private and out of Git; only `.env.example` belongs in the repository.

## Run one order

Pass the order text directly:

```bash
python tasks/orders/main.py --ask "Please send 2 individual CAB-1 cables"
```

Or start a prompt and enter the message:

```bash
python tasks/orders/main.py --ask
```

The app prints a JSON result and saves it to the queue. If you omit an order reference, it creates a stable reference from the message. You can supply one with `--order-ref PO-123`.

## Process the sample queue and open the review screen

Process the supplied seed requests and six additional examples with live model calls:

```bash
python tasks/orders/main.py
```

The command prints a summary and detailed outcomes. Then start the local review queue:

```bash
python tasks/orders/main.py --serve
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/). Filter by `ready`, `needs_clarification`, `failed`, or `duplicate`. Each entry shows the original message beside its result, local catalog evidence, validation findings, and whether a person reviewed it. For a ready draft, a reviewer can correct its SKU or quantity. A correction is checked against the catalog, re-priced, saved with the old and new values, and marked reviewed. The history is available at [http://127.0.0.1:8765/review-history](http://127.0.0.1:8765/review-history).

You may correct a draft from the command line as well:

```bash
python tasks/orders/main.py --review REQUEST_ID --field quantity --value 12
python tasks/orders/main.py --review REQUEST_ID --field sku --value CAB-2
```

## Verify behavior

Run five independent reference checks. This makes live model calls for the normal, unknown-product, and ambiguous-product/quantity examples; checks a duplicate; and applies a reviewer correction to the normal draft.

```bash
python tasks/orders/main.py --check
```

The command writes `reference-check-report.json`. It exits with status 0 if every check passes and 1 if any check fails. Expected totals in `reference-cases.json` were calculated from the domain rules, separately from application results.

Run the local unit checks without API calls:

```bash
cd tasks/orders
python -m unittest -v
```

The unit checks mock model extraction to exercise catalog matching, price calculations, package ambiguity handling, duplicate prevention, stable identity, reviewer correction persistence, and failed-call handling. They do not replace the live `--check`.

To revalidate responses already saved from real Groq calls, without making new calls:

```bash
python tasks/orders/main.py --replay-calls
```

This audit command only uses entries tagged `live_groq` in `model_calls.json`. Its results are explicitly labeled as cached real model responses and are written to `replayed_model_results.json`; it does not put replayed data into the operational queue.

## Code map and request flow

`main.py` is the small command-line entry point. The supporting modules are:

| Module | Responsibility |
| --- | --- |
| `config.py` | Loads the task-local `.env`, defines input/output paths and loads the catalog. |
| `llm.py` | Configures LangChain `ChatGroq`, sends the extraction prompt, parses JSON, and validates the model response schema. |
| `domain.py` | Performs local catalog matching, quantity/ambiguity validation, clarification drafting, and integer-cent pricing. |
| `storage.py` | Creates/migrates SQLite tables and saves request rows, model-call records, and JSON result snapshots. |
| `pipeline.py` | Loads request files, calls the model for each unique request, handles duplicate and failed records, summarizes results, and applies reviewer corrections. |
| `dashboard.py` | Serves the status-filtered local queue, correction forms, and correction-history page. |
| `checks.py` | Runs live reference cases in a temporary database and replays saved real model responses for audit. |
| `test_main.py` | Exercises domain, persistence, and failure handling with mocked model outputs. |

A request follows this sequence:

1. **Load input and catalog.** `pipeline.all_requests()` reads the original requests in `seed.json` and the six added requests in `requests.json`. The catalog comes from `config.py` and `seed.json`. Stable request IDs and original message text are retained. Malformed request entries are stored as failed inputs so other entries can still be processed.
2. **Ask the model to extract.** `llm.model_client()` configures LangChain `ChatGroq` using environment variables. `llm.extract_order()` requests JSON containing `product_query`, `quantity`, and `reason`. The model is told not to choose a SKU or infer package sizes. Missing credentials, model errors, malformed JSON, and invalid fields are recorded as failures; none cause a local parsing fallback.
3. **Look up local evidence.** `domain.local_catalog_lookup()` searches only the supplied catalog. A SKU or product phrase can return one, multiple, or no entries. The proposal records matching catalog entries as evidence. A model claim cannot add a product to the catalog.
4. **Validate and calculate.** `domain.evaluate()` keeps uncertain matches unresolved, requires a positive whole-number count, and rejects counts inferred from words such as “boxes” or “packs.” `domain.price()` uses the catalog unit price and applies a 10% discount to a line of at least 10 individual items, using integer cents and half-up rounding. The code creates clarification drafts for unresolved requests.
5. **Prevent duplicate drafts and save.** `pipeline.process_requests()` detects duplicate order references within the incoming batch and against saved requests. Reprocessing the same stable ID updates its database row instead of creating a second one. Unique non-duplicate requests are sent to the model. One request failing does not stop later requests. `storage.ensure_db()` creates or updates the SQLite tables; `storage.save_processed_results()` refreshes the JSON result snapshot.
6. **Review and correct.** `pipeline.review_correction()` permits corrections only to a ready draft. It validates the changed SKU or quantity, recalculates the price, updates the saved proposal, and adds a correction-history row. A structurally valid draft is still marked “not reviewed by a person” until a reviewer takes action.
7. **Show queue and analytics.** `dashboard.dashboard_page()` renders the status-filtered queue; `dashboard.Handler` serves the queue, accepts correction forms, and displays correction history. `pipeline.summary()` reports ready, clarification, failed, and duplicate counts, exception reasons, and the number of non-duplicate requests.
8. **Check and replay.** `checks.run_reference_check()` calls the live model and compares observed outcomes with the separate expected cases in a temporary database. `checks.replay_saved_model_calls()` revalidates only saved real responses and labels them as replay.

### Status meanings

- `ready`: a structurally valid proposal with one local catalog match and a validated item count; it still needs a person's review.
- `needs_clarification`: product or quantity is uncertain; the app saves a clarification draft and does not invent missing values.
- `failed`: an unusable request or a model/configuration/response error prevented a safe proposal.
- `duplicate`: another request with the same order reference already represents that order; it is not counted as a new order.

## Files and generated records

| File | Purpose |
| --- | --- |
| `main.py` | CLI entry point and command dispatch. |
| `config.py`, `llm.py`, `domain.py`, `storage.py`, `pipeline.py`, `dashboard.py`, `checks.py` | Application modules described in the code map above. |
| `domain.md` | Source-of-truth fictional business and pricing rules. |
| `seed.json` | Supplied catalog and original four requests. |
| `requests.json` | Six additional fictional email requests and their handwritten generation method. |
| `expected-seed-results.json` | Supplied expected outcomes for the original seed. |
| `reference-cases.json` | Five independently checked behaviors, including the corrected total. |
| `request.template.json` | Example shape for a single request. |
| `requirements.txt` | Python dependencies for the application. |
| `.env.example` | Safe list of configuration variables; contains no API key. |
| `orders.db` | Local durable request queue and reviewer correction history. |
| `processed_results.json` | Current queue snapshot in JSON. |
| `model_calls.json` | Model ID, timestamp, input, raw response, validated result, or model-call error. |
| `reference-check-report.json` | Results of the most recent live reference check. |
| `replayed_model_results.json` | Explicitly labeled audit replay of saved real responses. |
| `test_main.py` | Local tests using mocked model responses. |

The database and result/replay snapshots are generated local state. The task requires saving real model-call examples, so `model_calls.json` may be submitted after confirming it contains only fictional inputs and no credentials. Never submit `.env` or the virtual environment.

## Dataset, assumptions, and limits

The four starter requests are preserved, and six fictional examples extend the set to ten. Their wording varies while keeping the same catalog and pricing rules. A “usual cable” can match both cable products, so it remains unresolved. A package count is never converted to an individual-item quantity; even if the model proposes a count, a package word triggers clarification.

The supplied rules do not define a tie-breaker for partial product descriptions, package sizes, multiple order lines in one message, or attachment interpretation. This app supports one text order line per request. Attachment OCR, authentication, live email/ERP integration, and remote hosting are outside this implementation. The review server binds to localhost by default.

The practical improvement suggested by the examples is to ask customers for a catalog SKU and a count of individual items in the intake form. This would reduce both generic product matches and package-quantity clarifications.
