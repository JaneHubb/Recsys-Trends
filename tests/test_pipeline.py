"""pytest -q  (runs without external APIs)"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import aggregate, config, judge, venues
from pipeline.utils import Report, write_json, write_jsonl


# ---------- venues / tracks ----------

def test_page_count():
    assert venues.page_count("1234-1245") == 12
    assert venues.page_count("12:1-12:9") == 9
    assert venues.page_count("7") == 1
    assert venues.page_count(None) is None
    assert venues.page_count("x") is None


def test_normalize_track_rules():
    v = venues.get("SIGIR")
    assert venues.normalize_track(v, "Short Research Papers", "1200-1204") == ("short", True)
    assert venues.normalize_track(v, "Reproducibility Papers", "1-10") == ("resource", True)
    assert venues.normalize_track(v, "SIRIP: Industry Papers", "1-9") == ("applied", True)
    assert venues.normalize_track(v, "Keynote Talks", "1-2") == ("non_paper", True)
    # section says main, but 2 pages means a talk abstract
    assert venues.normalize_track(v, "Full Papers", "3-4") == ("non_paper", True)
    # no section → page-count rule
    assert venues.normalize_track(v, None, "1-5") == ("short", True)
    assert venues.normalize_track(v, None, "1-10") == ("main", True)
    # unknown section → flagged as unmatched
    assert venues.normalize_track(v, "Mystery Block", "1-10") == ("main", False)


def test_kdd_volumes():
    k = venues.get("KDD")
    assert venues.expected_volumes(k, 2024) == 1
    assert venues.expected_volumes(k, 2025) == 2


def test_source_for_mixed():
    n = venues.get("NeurIPS")
    assert venues.source_for(n, 2019) == "dblp"
    assert venues.source_for(n, 2023) == "openreview"
    assert [c["api"] for c in venues.openreview_configs(n, 2024)] == ["v2", "v2", "v2"]


# ---------- judge ----------

def test_parse_json_variants():
    assert judge.parse('{"label": "core", "confidence": "high", "reason": "sequential rec"}')["label"] == "core"
    r = judge.parse('Sure:\n{"label":"Related","confidence":"LOW","reason":"ad CTR"}\n')
    assert r == {"label": "related", "confidence": "low", "reason": "ad CTR"}
    assert judge.parse('{"label": "maybe"}') is None
    assert judge.parse("no json") is None


def test_kw_and_cid():
    assert judge.kw_hit("A Study of Click-Through Rate Prediction", None)
    assert not judge.kw_hit("Diffusion Models for Image Synthesis", "We generate images.")
    cid = judge.safe_cid("dblp:conf/kdd/LiX24")
    assert len(cid) <= 64 and all(c.isalnum() or c in "_-" for c in cid)
    assert judge.safe_cid("a") != judge.safe_cid("b")


def test_prompt_loads():
    system, template = judge.load_prompt()
    assert "core" in system and "{title}" in template
    p = {"id": "x", "venue": "KDD", "year": 2024, "title": "T"}
    assert "(not available)" in judge.build_user(template, p, None)


# ---------- aggregation ----------

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)


def _paper(i, venue, year, track="main"):
    return {"id": f"dblp:conf/{venue.lower()}/P{year}_{i}", "venue": venue, "year": year, "title": f"Paper {i}",
            "authors": ["A. Kim"], "doi": f"10.1/{venue}{year}.{i}", "url": None, "pages": "1-10", "track": track, "volume": "v"}


def _make_data(tmp: Path, cikm_2026: bool = False):
    raw = tmp / "raw"
    rows = []
    for v in ("KDD", "RecSys"):
        for y in (2024, 2025, 2026):
            n = 100 if v == "KDD" else 40
            papers = [_paper(i, v, y) for i in range(n)]
            papers.append(_paper(999, v, y, "demo"))
            write_json(raw / f"{v}-{y}.json", {"venue": v, "year": y, "source": "dblp", "collected": "t", "volumes": ["x"] * (2 if (v == "KDD" and y >= 2025) else 1), "sections": True, "papers": papers})
            if v == "KDD":
                for i in range(n):
                    label = "core" if i < 15 else ("related" if i < 20 else "no")
                    rows.append({"id": f"dblp:conf/kdd/P{y}_{i}", "label": label, "confidence": "high", "reason": "r", "kw": i < 25,
                                 "abs": True, "model": config.JUDGE_MODEL, "pv": config.PROMPT_VERSION})
    if cikm_2026:
        write_json(raw / "CIKM-2026.json", {"venue": "CIKM", "year": 2026, "source": "dblp", "collected": "t", "volumes": ["x"], "sections": True, "papers": [_paper(i, "CIKM", 2026) for i in range(30)]})
    write_jsonl(tmp / "llm" / "judge.jsonl", rows)
    # Sonnet flips P0 to related
    write_jsonl(tmp / "llm" / "rejudge.jsonl", [{"id": "dblp:conf/kdd/P2026_0", "label": "related", "confidence": "high", "reason": "s", "model": config.REJUDGE_MODEL, "pv": config.PROMPT_VERSION}])


def test_aggregate_end_to_end(tmp_path: Path):
    _make_data(tmp_path)
    s = aggregate.build(tmp_path, NOW, Report("t"), validate=True)
    kdd26 = s["counts"]["KDD"]["2026"]
    assert kdd26["total"] == 100 and kdd26["core"] == 14 and kdd26["related"] == 6 and kdd26["border"] == 5
    assert kdd26["state"] == "done"                      # August conference, complete by November
    assert s["counts"]["RecSys"]["2026"]["core"] == 40   # all core
    assert s["counts"]["SIGIR"]["2026"]["state"] == "planned"
    papers = json.loads((tmp_path / "papers" / "KDD-2026.json").read_text())
    assert len(papers["papers"]) == 20 and len(papers["border"]) == 5 and len(papers["demo"]) == 1
    assert any(p.get("rejudged") for p in papers["papers"])
    assert (tmp_path / "status.json").exists() and (tmp_path / "changelog.json").exists()
    # basis year: only 2 venues complete, below the 6-venue threshold, so it falls back to the latest year with any completion
    assert s["basis"]["year"] == 2026 and s["basis"]["kind"] == "provisional"


def test_state_rules(tmp_path: Path):
    _make_data(tmp_path, cikm_2026=True)
    s = aggregate.build(tmp_path, NOW, Report("t"), validate=False)
    # CIKM is held in November → not yet past on November 20
    assert s["counts"]["CIKM"]["2026"]["state"] == "part"
    # KDD 2026 expects 2 volumes and has 2 → done; 2024 expects 1
    assert s["counts"]["KDD"]["2024"]["state"] == "done"


def test_pending_blocks_done(tmp_path: Path):
    _make_data(tmp_path)
    # drop half the judgments → 'awaiting classification'
    rows = [json.loads(l) for l in (tmp_path / "llm" / "judge.jsonl").read_text().splitlines()]
    write_jsonl(tmp_path / "llm" / "judge.jsonl", rows[:150])
    s = aggregate.build(tmp_path, NOW, Report("t"), validate=False)
    assert s["counts"]["KDD"]["2026"]["state"] == "part"


def test_validation_blocks_big_drop(tmp_path: Path):
    _make_data(tmp_path)
    aggregate.build(tmp_path, NOW, Report("t"), validate=True)
    # halve KDD 2025 and re-aggregate → blocked
    p = tmp_path / "raw" / "KDD-2025.json"
    raw = json.loads(p.read_text())
    raw["papers"] = raw["papers"][:50]
    write_json(p, raw)
    with pytest.raises(aggregate.ValidationError):
        aggregate.build(tmp_path, NOW, Report("t"), validate=True)
    aggregate.build(tmp_path, NOW, Report("t"), validate=False)   # skipping is allowed


def test_next_update():
    assert aggregate.next_update(datetime(2026, 11, 20, tzinfo=timezone.utc)) == "2027-05-20"
    assert aggregate.next_update(datetime(2027, 1, 3, tzinfo=timezone.utc)) == "2027-05-20"
    assert aggregate.next_update(datetime(2027, 5, 21, tzinfo=timezone.utc)) == "2027-11-20"
    assert aggregate.month_passed(NOW, 2026, 8) and not aggregate.month_passed(NOW, 2026, 11)
