"""Application paths, environment loading, and shared catalog settings."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

CATALOG_PATH = ROOT / "seed.json"
REQUESTS_PATH = ROOT / "requests.json"
REFERENCES_PATH = ROOT / "reference-cases.json"
DB_PATH = ROOT / "orders.db"
CALLS_PATH = ROOT / "model_calls.json"
RESULTS_PATH = ROOT / "processed_results.json"
REPLAY_PATH = ROOT / "replayed_model_results.json"
REFERENCE_REPORT_PATH = ROOT / "reference-check-report.json"

CATALOG = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["catalog"]
MODEL_NAME = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
