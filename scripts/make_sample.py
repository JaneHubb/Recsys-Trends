"""Preview the whole aggregation and site with synthetic data (no external APIs, no LLM).

  python scripts/make_sample.py            → writes raw + llm files to sample/ and aggregates
  bash scripts/build_site.sh sample        → assembles the site in _site/
  python -m http.server -d _site 8000      → http://localhost:8000

Every number, title and author is fictional.
"""
from __future__ import annotations

import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "sample"
os.environ["RECSYS_DATA_DIR"] = str(OUT / "data")
os.environ["RECSYS_STATE_DIR"] = str(OUT / "state")

from pipeline import aggregate, config, venues  # noqa: E402
from pipeline.utils import Report, write_json, write_jsonl  # noqa: E402

R = random.Random(20261120)
NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
YEARS = list(range(2018, 2027))
REC = {"RecSys": [61, 75, 84, 92, 101, 118, 126, 138, 146], "SIGIR": [72, 88, 104, 118, 131, 152, 171, 186, 198], "KDD": [48, 61, 72, 81, 94, 102, 121, 134, 151],
       "WWW": [58, 66, 81, 92, 104, 118, 132, 141, 156], "WSDM": [22, 27, 31, 34, 38, 41, 47, 52, 55], "CIKM": [64, 79, 95, 112, 128, 141, 158, 172, 0],
       "NeurIPS": [9, 12, 16, 21, 27, 34, 46, 58, 71], "ICLR": [4, 6, 9, 12, 16, 22, 31, 44, 52]}
TOTAL = {"RecSys": None, "SIGIR": [211, 247, 288, 318, 365, 412, 496, 573, 640], "KDD": [293, 320, 350, 394, 449, 527, 698, 870, 980], "WWW": [170, 225, 217, 354, 323, 365, 405, 452, 507],
         "WSDM": [84, 91, 109, 112, 159, 123, 111, 171, 186], "CIKM": [402, 456, 520, 602, 720, 830, 940, 1020, 0], "NeurIPS": [1011, 1428, 1900, 2334, 2672, 3540, 4497, 5290, 5980], "ICLR": [337, 502, 687, 860, 1095, 1574, 2260, 3646, 4900]}
METHODS = ["Graph Neural Networks for", "Contrastive Learning for", "Large Language Models for", "Diffusion Models for", "Transformer Encoders for", "Semantic-ID Generation for", "LLM Agents for", "Contextual Bandits for", "Neural Collaborative Filtering for"]
TASKS = ["Sequential Recommendation", "Top-N Recommendation", "Click-Through Rate Prediction", "Conversational Recommendation", "Cross-Domain Recommendation", "Cold-Start Recommendation", "Bundle Recommendation", "Next-POI Recommendation"]
OTHER = ["Dense Retrieval with Hard Negatives", "Scaling Laws for Vision Transformers", "Robust Graph Learning under Noise", "Efficient Attention for Long Documents", "Causal Discovery from Observational Data", "Federated Optimization with Heterogeneous Clients"]
RELATED = ["Bid Shading for First-Price Display Advertising", "Learning to Rank for Web Search with Click Models", "Personalized Text Generation with Retrieval-Augmented LLMs", "Query Auto-Completion with Session Context"]
BORDER = [("User Intent Modeling for E-commerce Search", "search intent modeling, no recommendation"), ("Counterfactual Estimation for Ad Click Logs", "counterfactual estimation on ad logs"), ("Link Prediction on Bipartite Interaction Graphs", "generic link prediction, only uses rec datasets")]
SURN = ["Kim", "Lee", "Park", "Chen", "Wang", "Zhang", "Liu", "Smith", "Garcia", "Tanaka", "Singh", "Nguyen", "Müller"]
TRACKS = {"RecSys": [("main", 70), ("short", 20), ("resource", 10)], "SIGIR": [("main", 55), ("short", 30), ("resource", 10), ("applied", 5)], "KDD": [("main", 60), ("applied", 40)],
          "WWW": [("main", 85), ("applied", 15)], "WSDM": [("main", 100)], "CIKM": [("main", 50), ("short", 30), ("applied", 15), ("resource", 5)], "NeurIPS": [("main", 95), ("resource", 5)], "ICLR": [("main", 100)]}


def pick_w(items):
    tot = sum(w for _, w in items); r = R.random() * tot
    for it, w in items:
        r -= w
        if r <= 0:
            return it
    return items[-1][0]


def authors():
    n = 2 + R.randrange(5)
    return [f"{chr(65 + R.randrange(26))}. {R.choice(SURN)}" for _ in range(n)]


rows = []
for vid in venues.venue_ids():
    vcfg = venues.get(vid)
    for i, y in enumerate(YEARS):
        n_rec = REC[vid][i]
        if n_rec == 0:
            continue
        n_total = n_rec if vcfg.get("all_core") else TOTAL[vid][i]
        n_total = min(n_total, n_rec + 400)          # keep sample files small
        papers = []
        for k in range(n_total):
            is_rec = k < n_rec
            pid = f"dblp:conf/{vid.lower()}/S{y}_{k}" if vid not in ("ICLR",) and not (vid == "NeurIPS" and y >= 2021) else f"or:{vid[:2].lower()}{y}{k}"
            hot = y >= 2023 and R.random() < 0.5
            if is_rec:
                m = R.choice(METHODS[2:7] if hot else METHODS)
                title = f"{m} {R.choice(TASKS)}"
                kind = "core"
            elif k < n_rec + int(n_rec * 0.13):
                title = R.choice(RELATED); kind = "related"
            elif k < n_rec + int(n_rec * 0.13) + 4:
                title, reason = R.choice(BORDER); kind = "border"
            else:
                title = R.choice(OTHER) + f" ({k})"; kind = "no"
            track = pick_w(TRACKS[vid])
            p = {"id": pid, "venue": vid, "year": y, "title": title, "authors": authors(), "doi": None if pid.startswith("or:") else f"10.1145/{y}.{k}",
                 "url": f"https://openreview.net/forum?id=x{k}" if pid.startswith("or:") else f"https://doi.org/10.1145/{y}.{k}", "pages": "1-10", "track": track, "volume": "sample",
                 "arxiv": f"{str(y)[2:]}0{R.randrange(1000, 9999)}" if R.random() < 0.5 else None, "cites": int(R.expovariate(1 / (6 * (2026 - y) + 3)))}
            if pid.startswith("or:"):
                p["openreview"] = pid[3:]
                p["badge"] = "oral" if R.random() < 0.03 else ("spotlight" if R.random() < 0.09 else None)
            papers.append(p)
            if not vcfg.get("all_core"):
                label = {"core": "core", "related": "related", "border": "no", "no": "no"}[kind]
                rows.append({"id": pid, "label": label, "confidence": "low" if R.random() < 0.04 else "high", "reason": reason if kind == "border" else R.choice(["sequential recommendation task", "experiments on recommendation benchmarks", "advertising CTR, not recommendation", "generic search ranking"]),
                             "kw": kind in ("core", "related", "border"), "abs": True, "model": config.JUDGE_MODEL, "pv": config.PROMPT_VERSION, "ts": "sample"})
        volumes = ["sample-1", "sample-2"] if (vid == "KDD" and y >= 2025) else ["sample"]
        write_json(OUT / "data" / "raw" / f"{vid}-{y}.json", {"venue": vid, "year": y, "source": "sample", "collected": NOW.isoformat(), "volumes": volumes, "sections": True, "papers": papers}, indent=0)

write_jsonl(OUT / "data" / "llm" / "judge.jsonl", rows)
write_json(OUT / "data" / "meta.json", {"last_update": "2026-11-20", "last_run_id": "sample"})
rep = Report("sample")
aggregate.build(OUT / "data", NOW, rep, validate=False, run_kind="sample")
print(rep.render())
print(f"→ written to {OUT}/data. Run 'bash scripts/build_site.sh {OUT}' to assemble the site")
