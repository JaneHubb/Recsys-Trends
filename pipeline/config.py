"""Paths, constants, environment variables. Other modules read settings only from here."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("RECSYS_DATA_DIR", ROOT / "data"))
STATE_DIR = Path(os.environ.get("RECSYS_STATE_DIR", ROOT / "state"))
WORK_DIR = Path(os.environ.get("RECSYS_WORK_DIR", ROOT / "work"))   # temporary files such as abstracts; never committed
PROMPTS_DIR = ROOT / "prompts"
VENUES_FILE = ROOT / "venues.yaml"

# LLM
PROMPT_VERSION = "v1"                       # prompts/judge_{PROMPT_VERSION}.md
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "claude-haiku-5-5")
REJUDGE_MODEL = os.environ.get("REJUDGE_MODEL", "claude-sonnet-5-5")
MAX_RUN_USD = float(os.environ.get("MAX_RUN_USD", "9.5"))     # LLM cost cap per run (based on the estimate)
QUALITY_SAMPLE = int(os.environ.get("QUALITY_SAMPLE", "300"))  # Sonnet quality-sample size (results go to data/quality.json only)
BATCH_CHUNK = 8000                           # requests per Batch (stays well under the 256 MB limit)
JUDGE_MAX_TOKENS = 160
ABSTRACT_MAX_CHARS = 2500

# Batch API prices (USD per million tokens). Update when changing models
PRICES = {
    "claude-haiku-5-5": {"in": 0.05, "out": 0.25},
    "claude-sonnet-5-5": {"in": 1.00, "out": 5.00},
}
CHARS_PER_TOKEN = 3.0                        # conservative (the newer tokenizer yields more tokens than before)

# collection
USER_AGENT = os.environ.get(
    "RECSYS_USER_AGENT",
    "recsys-trends/0.1 (GitHub Actions; research use; contact via repository issues)",
)
S2_API_KEY = os.environ.get("S2_API_KEY", "")
S2_FIELDS = "paperId,abstract,citationCount,externalIds"
S2_BATCH = 500
DBLP_API = "https://dblp.org/search/publ/api"
DBLP_WEB = "https://dblp.org/db"
OPENREVIEW_V1 = "https://api.openreview.net"
OPENREVIEW_V2 = "https://api2.openreview.net"
S2_API = "https://api.semanticscholar.org/graph/v1"

# update schedule (month, day)
UPDATE_DATES = [(5, 20), (11, 20)]

# recommender-related keywords (borderline cases: papers with a keyword that the LLM still labels 'no' are shown separately)
KEYWORD_PATTERN = (
    r"recommend|recsys|collaborative filtering|click[- ]?through|\bctr\b|personali[sz]|"
    r"user model|implicit feedback|next[- ]item|session[- ]based|matrix factori|user[- ]item|"
    r"cold[- ]start|user preference|preference learning|bandit|user interest|interest model"
)
