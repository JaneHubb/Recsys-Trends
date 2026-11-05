"""Batch API 를 가짜 클라이언트로 바꿔 상태 머신 전체(제출 → 수거 → 재판정 → 집계)를 돌린다."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

from pipeline import collect_openreview, config, enrich_s2, judge, run, state
from pipeline.utils import Report, read_jsonl, write_json, write_jsonl


class FakeBatches:
    def __init__(self):
        self.store: dict[str, list] = {}

    def create(self, requests):
        bid = f"msgbatch_{len(self.store)}"
        self.store[bid] = requests
        return NS(id=bid, processing_status="in_progress")

    def retrieve(self, bid):
        n = len(self.store[bid])
        return NS(id=bid, processing_status="ended", request_counts=NS(processing=0, succeeded=n, errored=0, canceled=0, expired=0))

    def results(self, bid):
        for req in self.store[bid]:
            user = req["params"]["messages"][0]["content"]
            title = user.split("Title: ", 1)[1].split("\n", 1)[0]
            if "Recommend" in title:
                label, conf = "core", "high"
            elif "Advertis" in title:
                label, conf = "related", "low"
            else:
                label, conf = "no", "high"
            if "Bandit" in title:
                conf = "low"
            text = json.dumps({"label": label, "confidence": conf, "reason": "가짜"})
            yield NS(custom_id=req["custom_id"], result=NS(type="succeeded", message=NS(
                content=[NS(text=text)], usage=NS(input_tokens=900, output_tokens=40, cache_read_input_tokens=100, cache_creation_input_tokens=0))))


def _setup(tmp: Path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp / "data")
    monkeypatch.setattr(config, "STATE_DIR", tmp / "state")
    monkeypatch.setattr(config, "WORK_DIR", tmp / "work")
    monkeypatch.setattr(config, "QUALITY_SAMPLE", 6)
    fake = NS(messages=NS(batches=FakeBatches()))
    monkeypatch.setattr(judge, "client", lambda: fake)
    monkeypatch.setattr(enrich_s2, "enrich", lambda papers, report, http=None: ({}, {}))
    monkeypatch.setattr(collect_openreview, "fetch_abstracts", lambda http, ids: {})
    titles = ["Recommending Items with Graphs", "Advertising Bid Shading", "Bandit Recommendation", "Protein Folding", "Advertising CTR with Bandits", "Image Segmentation"]
    papers, abstracts = [], {}
    for y in (2025, 2026):
        ps = []
        for i, t in enumerate(titles * 3):
            pid = f"dblp:conf/kdd/K{y}_{i}"
            ps.append({"id": pid, "venue": "KDD", "year": y, "title": f"{t} {i}", "authors": ["X"], "doi": f"10.1/{y}.{i}", "url": None, "pages": "1-10", "track": "main", "volume": "v"})
            abstracts[pid] = "abstract " + t
        write_json(tmp / "data" / "raw" / f"KDD-{y}.json", {"venue": "KDD", "year": y, "source": "dblp", "collected": "t", "volumes": ["a", "b"], "sections": True, "papers": ps})
        papers += ps
    write_json(tmp / "data" / "raw" / "RecSys-2026.json", {"venue": "RecSys", "year": 2026, "source": "dblp", "collected": "t", "volumes": ["a"], "sections": True,
                                                            "papers": [{"id": "dblp:conf/recsys/R1", "venue": "RecSys", "year": 2026, "title": "R", "authors": [], "doi": None, "url": None, "pages": "1-9", "track": "main", "volume": "v"}]})
    write_jsonl(tmp / "work" / "abstracts.jsonl", ({"id": k, "abstract": v} for k, v in abstracts.items()))
    return fake, abstracts


def test_full_cycle(tmp_path: Path, monkeypatch):
    fake, abstracts = _setup(tmp_path, monkeypatch)
    st = state.load()
    report = Report("t")
    pairs = [("KDD", 2025), ("KDD", 2026), ("RecSys", 2026)]
    assert run.cmd_judge_submit(pairs, abstracts, st, report, force=False, dry_run=False)
    assert st["phase"] == "judge_submitted" and len(st["batches"]) == 1
    assert (tmp_path / "state" / "pending_extra.json").exists()

    run.cmd_advance(st, report, wait_minutes=0, validate=False)
    assert st["phase"] == "idle"
    judged = read_jsonl(tmp_path / "data" / "llm" / "judge.jsonl")
    assert len(judged) == 36 and all(j["id"].startswith("dblp:conf/kdd/") for j in judged)
    assert all("kw" in j and "abs" in j for j in judged)
    rejudged = read_jsonl(tmp_path / "data" / "llm" / "rejudge.jsonl")
    low = [j for j in judged if j["confidence"] == "low"]
    assert len(rejudged) >= len(low) > 0                      # 저확신 전부 + 품질 표본
    q = json.loads((tmp_path / "data" / "quality.json").read_text())["latest"]
    assert q["n_sample"] > 0 and q["agreement"] == 1.0         # 같은 가짜 판정이라 일치율 1.0
    summary = json.loads((tmp_path / "data" / "summary.json").read_text())
    assert summary["counts"]["KDD"]["2026"]["core"] == 6       # 'Recommend' 포함 제목 6편 ('Bandit Recommendation' 포함)
    assert summary["counts"]["KDD"]["2026"]["related"] == 6
    assert summary["counts"]["RecSys"]["2026"]["core"] == 1
    assert not (tmp_path / "state" / "pending_extra.json").exists()
    assert report.deploy and (tmp_path / "data" / "meta.json").exists()
    assert st["spent_usd"] == 0.0 and "비용" in st["notes"][-1]      # reset 뒤 새 상태

    # 두 번째 실행: 전부 캐시되어 제출할 것이 없다
    st2 = state.load()
    assert not run.cmd_judge_submit(pairs, abstracts, st2, Report("t2"), force=False, dry_run=False)


def test_budget_defers(tmp_path: Path, monkeypatch):
    fake, abstracts = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(judge, "estimate", lambda papers, abstracts, model, system, template: (0.001 * len(papers), 0, 0))
    reserve = (36 * 0.03 + 6) * run.SONNET_PER_PAPER_USD
    monkeypatch.setattr(config, "MAX_RUN_USD", reserve + 0.0185)   # 2026년 18편만 들어가는 예산
    st = state.load()
    report = Report("t")
    run.cmd_judge_submit([("KDD", 2025), ("KDD", 2026)], abstracts, st, report, force=False, dry_run=False)
    assert st["deferred"] > 0
    submitted = fake.messages.batches.store[st["batches"][0]]
    years = {json.loads(open(tmp_path / "state" / "pending_extra.json").read())[r["custom_id"]]["id"].split("K")[1][:4] for r in submitted}
    assert years == {"2026"}                                   # 최신 연도 우선


def test_dry_run_submits_nothing(tmp_path: Path, monkeypatch):
    fake, abstracts = _setup(tmp_path, monkeypatch)
    st = state.load()
    assert not run.cmd_judge_submit([("KDD", 2026)], abstracts, st, Report("t"), force=False, dry_run=True)
    assert st["phase"] == "idle" and not fake.messages.batches.store


def test_all_errored_batch_aborts_and_keeps_state(tmp_path: Path, monkeypatch):
    fake, abstracts = _setup(tmp_path, monkeypatch)

    class ErrBatches(FakeBatches):
        def results(self, bid):
            for req in self.store[bid]:
                yield NS(custom_id=req["custom_id"], result=NS(type="errored", error="not_found: model"))
    fake.messages.batches = ErrBatches()
    st = state.load()
    run.cmd_judge_submit([("KDD", 2026)], abstracts, st, Report("t"), force=False, dry_run=False)
    import pytest
    with pytest.raises(RuntimeError):
        run.cmd_advance(st, Report("t"), wait_minutes=0, validate=False)
    assert state.load()["phase"] == "judge_submitted"          # 상태 유지 → 원인 수정 후 advance 로 재수거
    assert not (tmp_path / "data" / "llm" / "judge.jsonl").exists()


def test_preflight_bad_model(monkeypatch):
    class NotFoundError(Exception):
        pass
    cl = NS(models=NS(retrieve=lambda m: (_ for _ in ()).throw(NotFoundError("404 model not found"))))
    import pytest
    with pytest.raises(SystemExit):
        judge.preflight(cl, ["nope"])
    ok = NS(models=NS(retrieve=lambda m: NS(id=m)))
    judge.preflight(ok, ["claude-haiku-5-5"])   # 예외 없음
