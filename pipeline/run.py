"""CLI 진입점.

  python -m pipeline.run update   [--years 2025,2026] [--venues KDD,SIGIR] [--dry-run] [--force] [--wait-minutes N]
  python -m pipeline.run advance  [--wait-minutes N]      진행 중인 Batch 를 이어받아 끝까지 처리
  python -m pipeline.run monthly                          heartbeat · 소스 점검 · 재집계
  python -m pipeline.run aggregate [--no-validate]        집계만 다시
  python -m pipeline.run collect  [--years ...] [--venues ...]   수집만 (초록은 work/ 에)
  python -m pipeline.run status                           상태 출력
"""
from __future__ import annotations

import argparse
import random
import sys
import time
import traceback
from datetime import datetime, timezone

from . import aggregate, collect_dblp, collect_openreview, config, cost, enrich_s2, judge, state, venues
from .utils import Http, Report, append_jsonl, log, now_iso, read_json, read_jsonl, setup_logging, write_error, write_json, write_jsonl

SONNET_PER_PAPER_USD = cost.estimate_usd(config.REJUDGE_MODEL, 1300, 80)   # 재판정 1편 추정치


def _now() -> datetime:
    return datetime.now(timezone.utc)


now_iso_dt = _now


def _parse_list(s: str | None, cast=str) -> list:
    return [cast(x.strip()) for x in (s or "").split(",") if x.strip()]


# ---------- 범위 ----------

def scope_pairs(years: list[int], vids: list[str], now: datetime) -> list[tuple[str, int]]:
    """명시된 범위가 없으면 '집계 완료가 아닌' 학회·연도 전체 (첫 실행 = 백필)"""
    cfg = venues.load()
    all_v = venues.venue_ids()
    vids = vids or all_v
    unknown = [v for v in vids if v not in cfg["venues"]]
    if unknown:
        raise SystemExit(f"venues.yaml 에 없는 학회: {unknown}")
    status = (read_json(config.DATA_DIR / "status.json", {}) or {}).get("venues", {})
    pairs: list[tuple[str, int]] = []
    for v in vids:
        vcfg = cfg["venues"][v]
        for y in (years or range(venues.start_year(), now.year + 1)):
            if years:
                pairs.append((v, y))
                continue
            st = status.get(v, {}).get(str(y), {}).get("state")
            if st == "done":
                continue
            if y == now.year and not aggregate.month_passed(now, y, vcfg["month"]) and venues.source_for(vcfg, y) == "dblp":
                continue   # 아직 열리지 않은 학회는 DBLP 에 없다. OpenReview 학회는 결정이 먼저 나오므로 시도한다
            pairs.append((v, y))
    return pairs


# ---------- 수집 ----------

def cmd_collect(pairs: list[tuple[str, int]], report: Report) -> dict[str, str]:
    """raw 파일을 쓰고, 초록은 work/abstracts.jsonl 에 둔다. 초록 dict 를 돌려준다.
    범위 안의 모든 학회·연도가 실패하면 예외를 던진다 (소스 장애 → Issue)"""
    cfg = venues.load()
    dblp_http = Http(min_interval=1.2)
    or_http = Http(min_interval=0.5)
    abstracts: dict[str, str] = {}
    n_fail = 0
    report.section("수집")
    for vid, y in pairs:
        vcfg = cfg["venues"][vid]
        src = venues.source_for(vcfg, y)
        try:
            if src == "dblp":
                res = collect_dblp.collect(dblp_http, vcfg, y, report)
                papers = res["papers"]
                if papers and not vcfg.get("all_core"):
                    meta, abs_ = enrich_s2.enrich(papers, report)
                    for p in papers:
                        p.update(meta.get(p["id"], {}))
                    abstracts.update(abs_)
                elif papers:   # RecSys: 링크용 arXiv·인용수만
                    meta, _ = enrich_s2.enrich(papers, report)
                    for p in papers:
                        p.update(meta.get(p["id"], {}))
                raw = {"venue": vid, "year": y, "source": "dblp", "collected": now_iso(),
                       "volumes": res["volumes"], "sections": res["sections"], "papers": papers}
            else:
                orcfgs = venues.openreview_configs(vcfg, y)
                if not orcfgs:
                    report.warn(f"{vid} {y}: openreview 설정 없음")
                    continue
                res = collect_openreview.collect(or_http, vcfg, y, orcfgs, report)
                papers = res["papers"]
                abstracts.update(res["abstracts"])
                raw = {"venue": vid, "year": y, "source": "openreview", "collected": now_iso(),
                       "volumes": res["volumes"], "sections": True, "papers": papers}
                if not papers and vcfg.get("dblp", {}).get("tocs") and aggregate.month_passed(now_iso_dt(), y, vcfg["month"]):
                    # OpenReview 에서 0편이고 학회가 이미 끝났으면 DBLP 로 대체 (API 변경 대비)
                    res = collect_dblp.collect(dblp_http, vcfg, y, report)
                    papers = res["papers"]
                    if papers:
                        report.warn(f"{vid} {y}: OpenReview 0편 → DBLP 로 대체 수집 {len(papers)}편 (oral/spotlight 배지 없음)")
                        meta, abs_ = enrich_s2.enrich(papers, report)
                        for p in papers:
                            p.update(meta.get(p["id"], {}))
                        abstracts.update(abs_)
                        raw = {"venue": vid, "year": y, "source": "dblp", "collected": now_iso(),
                               "volumes": res["volumes"], "sections": res["sections"], "papers": papers}
        except Exception as e:  # noqa: BLE001
            n_fail += 1
            report.warn(f"{vid} {y} 수집 실패: {e.__class__.__name__}: {str(e)[:160]}")
            log.debug(traceback.format_exc())
            continue
        if not papers:
            report.info(f"{vid} {y}: 0편 (아직 없음)")
            continue
        counted = sum(1 for p in papers if p["track"] in venues.counted_tracks())
        tracks = {}
        for p in papers:
            tracks[p["track"]] = tracks.get(p["track"], 0) + 1
        report.info(f"{vid} {y}: {len(papers)}편 수집, 집계 대상 {counted}편 · " + ", ".join(f"{k} {n}" for k, n in sorted(tracks.items())))
        write_json(config.DATA_DIR / "raw" / f"{vid}-{y}.json", raw, indent=0)
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(config.WORK_DIR / "abstracts.jsonl", ({"id": k, "abstract": v} for k, v in abstracts.items()))
    if pairs and n_fail == len(pairs):
        raise RuntimeError(f"범위 안의 {len(pairs)}개 학회·연도 수집이 모두 실패했습니다. DBLP·OpenReview 접속 상태를 확인하세요 (보고서의 경고 참고)")
    return abstracts


def load_work_abstracts() -> dict[str, str]:
    return {r["id"]: r["abstract"] for r in read_jsonl(config.WORK_DIR / "abstracts.jsonl")}


def raw_index(pairs: list[tuple[str, int]] | None = None) -> dict[str, dict]:
    """id → 원본 레코드 (판별 대상 트랙만)"""
    counted = set(venues.counted_tracks())
    out: dict[str, dict] = {}
    for (vid, y), raw in aggregate.load_raw(config.DATA_DIR).items():
        if pairs is not None and (vid, y) not in pairs:
            continue
        for p in raw.get("papers", []):
            if p.get("track") in counted:
                out[p["id"]] = p
    return out


# ---------- 판별 제출 ----------

def cmd_judge_submit(pairs: list[tuple[str, int]], abstracts: dict[str, str], st: dict, report: Report,
                     force: bool, dry_run: bool) -> bool:
    cfg = venues.load()
    cache = {} if force else judge.load_cache(config.DATA_DIR / "llm" / "judge.jsonl", config.JUDGE_MODEL, config.PROMPT_VERSION)
    idx = raw_index(pairs)
    todo = [p for p in idx.values() if not cfg["venues"][p["venue"]].get("all_core") and p["id"] not in cache]
    todo.sort(key=lambda p: (-p["year"], p["venue"], p["id"]))
    system, template = judge.load_prompt()
    report.section("LLM 판별")
    if not todo:
        report.info("판별할 논문이 없습니다 (모두 캐시됨)")
        return False
    # 예산: Haiku 추정 + Sonnet 예비(저확신 3% + 품질 표본) ≤ MAX_RUN_USD. 넘으면 최신 연도부터 넣고 나머지는 미룬다
    reserve = (len(todo) * 0.03 + config.QUALITY_SAMPLE) * SONNET_PER_PAPER_USD
    budget = config.MAX_RUN_USD - reserve
    selected: list[dict] = []
    running = 0.0
    for p in todo:
        usd, _, _ = judge.estimate([p], abstracts, config.JUDGE_MODEL, system, template)
        if running + usd > budget:
            break
        running += usd
        selected.append(p)
    deferred = len(todo) - len(selected)
    n_abs = sum(1 for p in selected if p["id"] in abstracts)
    report.info(f"판별 대상 {len(todo):,}편 (초록 있음 {n_abs:,}) · 이번 실행 {len(selected):,}편, 예상 {running:.2f}달러 "
                f"(Sonnet 예비 {reserve:.2f}달러 포함 상한 {config.MAX_RUN_USD}달러)")
    if deferred:
        report.warn(f"예산 상한으로 {deferred:,}편을 다음 실행으로 미룹니다 (최신 연도 우선). 다음 달 수동 실행으로 이어서 처리하세요")
    if dry_run:
        report.info("dry-run: Batch 를 제출하지 않았습니다")
        return False
    cl = judge.client()
    judge.preflight(cl, [config.JUDGE_MODEL, config.REJUDGE_MODEL])
    reqs = [judge.build_request(p, abstracts.get(p["id"]), config.JUDGE_MODEL, system, template) for p in selected]
    extra = {judge.safe_cid(p["id"]): {"id": p["id"], "kw": judge.kw_hit(p["title"], abstracts.get(p["id"])), "abs": p["id"] in abstracts} for p in selected}
    write_json(config.STATE_DIR / "pending_extra.json", extra)
    st.update({"phase": "judge_submitted", "run_id": _now().strftime("%Y%m%d-%H%M"), "started": now_iso(),
               "batches": [], "estimate_usd": round(running, 3), "deferred": deferred})
    st["scope"] = {"years": sorted({y for _, y in pairs}), "venues": sorted({v for v, _ in pairs}), "force": force}

    def on_batch(bid: str) -> None:          # 청크마다 바로 저장: 중간에 실패해도 제출된 Batch 를 잃지 않는다
        st["batches"].append(bid)
        state.save(st)

    judge.submit(cl, reqs, on_batch=on_batch)
    state.note(st, f"Haiku 판별 제출 {len(selected)}편, batch {len(st['batches'])}건")
    state.save(st)
    return True


# ---------- 진행 ----------

def _wait_or_break(deadline: float, report: Report, what: str) -> bool:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        report.info(f"{what} 아직 처리 중 — 대기 시간이 끝나 종료합니다. 다음 advance 실행이 이어받습니다")
        return False
    time.sleep(min(300, max(30, remaining)))
    return True


def _check_batch_errors(rows: list[dict], errors: list[dict], st: dict, report: Report, label: str) -> None:
    """오류 행을 기록하고, 전부 실패했으면 실행을 중단한다 (상태는 유지되어 원인 수정 후 advance 로 재수거 가능)"""
    if errors:
        append_jsonl(config.DATA_DIR / "llm" / "errors.jsonl", [{**e, "ts": now_iso(), "run": st.get("run_id")} for e in errors])
    if errors and not rows:
        raise RuntimeError(f"{label} Batch 결과가 전부 실패했습니다 (예: {errors[0].get('type')} {errors[0].get('error')}). "
                           f"모델 ID({config.JUDGE_MODEL}, {config.REJUDGE_MODEL})와 API 키를 확인하세요")
    if errors and len(errors) > 0.05 * (len(rows) + len(errors)):
        report.warn(f"{label} Batch 오류 {len(errors)}건 ({len(errors) / (len(rows) + len(errors)):.0%}) — data/llm/errors.jsonl 참고. 실패한 논문은 다음 실행에서 다시 판별합니다")


def step_fetch_judge(cl, st: dict, report: Report) -> None:
    extra = read_json(config.STATE_DIR / "pending_extra.json", {}) or {}
    rows, errors, usage = judge.collect_results(cl, st["batches"], {}, config.JUDGE_MODEL, config.PROMPT_VERSION, extra)
    for r in rows:
        r["ts"] = now_iso()
        r["run"] = st["run_id"]
    _check_batch_errors(rows, errors, st, report, "Haiku")
    append_jsonl(config.DATA_DIR / "llm" / "judge.jsonl", rows)
    state.add_usage(st, config.JUDGE_MODEL, usage)
    spent = cost.actual_usd(config.JUDGE_MODEL, usage)
    st["spent_usd"] = round(st.get("spent_usd", 0) + spent, 4)
    by = {}
    for r in rows:
        by[r["label"]] = by.get(r["label"], 0) + 1
    report.info(f"Haiku 결과 {len(rows):,}편 (core {by.get('core', 0):,} · related {by.get('related', 0):,} · no {by.get('no', 0):,}) · "
                f"오류 {len(errors)} · 실제 비용 {spent:.2f}달러")
    low = [r["id"] for r in rows if r["confidence"] == "low"]
    st["rejudge_ids"] = low
    rng = random.Random(st["run_id"])
    pool = [r for r in rows if r["id"] not in set(low)]
    per = max(1, config.QUALITY_SAMPLE // 3)
    sample: list[str] = []
    for lab in judge.LABELS:
        group = [r["id"] for r in pool if r["label"] == lab]
        rng.shuffle(group)
        sample.extend(group[:per])
    st["quality_ids"] = sample
    st["phase"] = "judged"
    state.note(st, f"Haiku 완료 {len(rows)}편, 저확신 {len(low)}편, 품질 표본 {len(sample)}편")
    state.save(st)


def _fetch_abstracts_for(ids: list[str], report: Report) -> dict[str, str]:
    idx = raw_index()
    recs = [idx[i] for i in ids if i in idx]
    abstracts = load_work_abstracts()
    missing = [r for r in recs if r["id"] not in abstracts]
    if missing:
        _, abs_ = enrich_s2.enrich([r for r in missing if r.get("doi")], report)
        abstracts.update(abs_)
        or_ids = [r["openreview"] for r in missing if r.get("openreview")]
        if or_ids:
            abstracts.update(collect_openreview.fetch_abstracts(Http(min_interval=0.4), or_ids))
    return abstracts


def step_submit_rejudge(cl, st: dict, report: Report) -> None:
    idx = raw_index()
    remaining = config.MAX_RUN_USD - st.get("spent_usd", 0.0)
    low = [i for i in st.get("rejudge_ids", []) if i in idx]
    low.sort(key=lambda i: -idx[i]["year"])
    n_afford = int(max(0.0, remaining) / SONNET_PER_PAPER_USD)
    if n_afford < len(low):
        report.warn(f"예산이 남지 않아 저확신 재판정 {len(low)}편 중 {n_afford}편만 처리합니다 (최신 연도 우선)")
        low = low[:n_afford]
    n_afford -= len(low)
    sample = [i for i in st.get("quality_ids", []) if i in idx][:max(0, n_afford)]
    if len(sample) < len(st.get("quality_ids", [])):
        report.warn(f"예산 때문에 품질 표본을 {len(sample)}편으로 줄였습니다")
    st["quality_ids"] = sample
    ids = list(dict.fromkeys(low + sample))
    if not ids:
        st["phase"] = "rejudged"
        state.note(st, "재판정 대상 없음")
        state.save(st)
        return
    abstracts = _fetch_abstracts_for(ids, report)
    system, template = judge.load_prompt()
    reqs = [judge.build_request(idx[i], abstracts.get(i), config.REJUDGE_MODEL, system, template) for i in ids]
    usd, _, _ = judge.estimate([idx[i] for i in ids], abstracts, config.REJUDGE_MODEL, system, template)
    extra = {judge.safe_cid(i): {"id": i, "kw": judge.kw_hit(idx[i]["title"], abstracts.get(i)), "abs": i in abstracts} for i in ids}
    write_json(config.STATE_DIR / "pending_extra.json", extra)
    st["rejudge_batches"] = []
    st["phase"] = "rejudge_submitted"

    def on_batch(bid: str) -> None:
        st["rejudge_batches"].append(bid)
        state.save(st)

    judge.submit(cl, reqs, on_batch=on_batch)
    state.note(st, f"Sonnet 재판정 제출 {len(ids)}편 (저확신 {len(low)} + 표본 {len(sample)}), 예상 {usd:.2f}달러")
    report.info(f"Sonnet 재판정 제출: 저확신 {len(low):,}편 + 품질 표본 {len(sample):,}편, 예상 {usd:.2f}달러")
    state.save(st)


def step_fetch_rejudge(cl, st: dict, report: Report) -> None:
    extra = read_json(config.STATE_DIR / "pending_extra.json", {}) or {}
    rows, errors, usage = judge.collect_results(cl, st["rejudge_batches"], {}, config.REJUDGE_MODEL, config.PROMPT_VERSION, extra)
    for r in rows:
        r["ts"] = now_iso()
        r["run"] = st["run_id"]
    _check_batch_errors(rows, errors, st, report, "Sonnet")
    append_jsonl(config.DATA_DIR / "llm" / "rejudge.jsonl", rows)
    state.add_usage(st, config.REJUDGE_MODEL, usage)
    spent = cost.actual_usd(config.REJUDGE_MODEL, usage)
    st["spent_usd"] = round(st.get("spent_usd", 0) + spent, 4)
    # 품질 (내부용): 표본에 대한 Haiku ↔ Sonnet 일치율
    haiku = judge.load_cache(config.DATA_DIR / "llm" / "judge.jsonl", config.JUDGE_MODEL, config.PROMPT_VERSION)
    sonnet = {r["id"]: r for r in rows}
    sample = [i for i in st.get("quality_ids", []) if i in sonnet and i in haiku]
    agree = [i for i in sample if haiku[i]["label"] == sonnet[i]["label"]]
    by_label: dict[str, dict] = {}
    for i in sample:
        b = by_label.setdefault(haiku[i]["label"], {"n": 0, "agree": 0})
        b["n"] += 1
        b["agree"] += int(haiku[i]["label"] == sonnet[i]["label"])
    idx = raw_index()
    q = {
        "ts": now_iso(), "run_id": st["run_id"], "models": [config.JUDGE_MODEL, config.REJUDGE_MODEL], "prompt_version": config.PROMPT_VERSION,
        "n_sample": len(sample), "agreement": round(len(agree) / len(sample), 4) if sample else None,
        "by_haiku_label": {k: {"n": v["n"], "agreement": round(v["agree"] / v["n"], 3)} for k, v in by_label.items()},
        "low_confidence_rejudged": len([i for i in st.get("rejudge_ids", []) if i in sonnet]),
        "disagreements": [{"id": i, "title": idx.get(i, {}).get("title"), "venue": idx.get(i, {}).get("venue"), "year": idx.get(i, {}).get("year"),
                           "haiku": haiku[i]["label"], "sonnet": sonnet[i]["label"], "reason": sonnet[i].get("reason")}
                          for i in sample if haiku[i]["label"] != sonnet[i]["label"]],
    }
    hist = read_json(config.DATA_DIR / "quality.json", {}) or {}
    history = (hist.get("history") or [])[-11:]
    if hist.get("latest"):
        history.append({k: hist["latest"].get(k) for k in ("ts", "run_id", "n_sample", "agreement")})
    write_json(config.DATA_DIR / "quality.json", {"latest": q, "history": history})
    report.info(f"Sonnet 결과 {len(rows):,}편 · 오류 {len(errors)} · 비용 {spent:.2f}달러 · "
                f"품질 표본 일치율 {q['agreement'] if q['agreement'] is not None else '-'} (data/quality.json, 내부용)")
    st["phase"] = "rejudged"
    state.note(st, f"Sonnet 완료 {len(rows)}편, 일치율 {q['agreement']}")
    state.save(st)


def step_finish(st: dict, report: Report, validate: bool, run_kind: str = "update") -> None:
    aggregate.build(config.DATA_DIR, _now(), report, validate=validate, run_kind=run_kind)
    meta = read_json(config.DATA_DIR / "meta.json", {}) or {}
    meta["last_update"] = _now().date().isoformat()
    meta["last_run_id"] = st.get("run_id")
    meta["last_spent_usd"] = st.get("spent_usd", 0.0)
    write_json(config.DATA_DIR / "meta.json", meta)
    report.info(f"이번 실행 LLM 비용(실제) {st.get('spent_usd', 0.0):.2f}달러 · 미룬 논문 {st.get('deferred', 0)}편")
    try:
        (config.STATE_DIR / "pending_extra.json").unlink()
    except FileNotFoundError:
        pass
    new = state.reset(st)
    state.note(new, f"완료 run {st.get('run_id')} · 비용 {st.get('spent_usd', 0.0):.2f}달러")
    state.save(new)
    st.clear()
    st.update(new)
    report.deploy = True


def cmd_advance(st: dict, report: Report, wait_minutes: float, validate: bool) -> None:
    deadline = time.monotonic() + wait_minutes * 60
    cl = None
    report.section("진행")
    while True:
        ph = st["phase"]
        if ph == "idle":
            report.info("진행 중인 작업이 없습니다")
            return
        if ph in ("judge_submitted", "rejudge_submitted"):
            cl = cl or judge.client()
            ids = st["batches"] if ph == "judge_submitted" else st["rejudge_batches"]
            s = judge.status(cl, ids)
            if not s["ended"]:
                c = s["counts"]
                log.info("Batch 처리 중: 완료 %d / 처리 중 %d", c["succeeded"], c["processing"])
                if not _wait_or_break(deadline, report, "Batch"):
                    return
                continue
            if ph == "judge_submitted":
                step_fetch_judge(cl, st, report)
            else:
                step_fetch_rejudge(cl, st, report)
            continue
        if ph == "judged":
            cl = cl or judge.client()
            step_submit_rejudge(cl, st, report)
            continue
        if ph == "rejudged":
            step_finish(st, report, validate)
            return
        raise RuntimeError(f"알 수 없는 phase: {ph}")


# ---------- 월간 ----------

def cmd_monthly(report: Report, validate: bool) -> None:
    report.section("월간 점검")
    write_json(config.DATA_DIR / "heartbeat.json", {"ts": now_iso(), "note": "예약 실행이 60일 비활성으로 꺼지지 않도록 매월 커밋"})
    health = {"ts": now_iso(), "sources": {}}
    checks = {
        "dblp": (config.DBLP_API, {"q": "toc:db/conf/recsys/recsys2023.bht:", "h": 1, "format": "json"}),
        "semantic_scholar": (f"{config.S2_API}/paper/DOI:10.1145/3604915.3608755", {"fields": "title"}),
        "openreview": (f"{config.OPENREVIEW_V2}/notes", {"content.venueid": "ICLR.cc/2024/Conference", "limit": 1}),
    }
    http = Http(min_interval=1.0, headers={"x-api-key": config.S2_API_KEY} if config.S2_API_KEY else None)
    for name, (url, params) in checks.items():
        try:
            r = http.get(url, params=params, retries=2, timeout=40)
            ok = r.status_code == 200
            health["sources"][name] = {"ok": ok, "status": r.status_code}
        except Exception as e:  # noqa: BLE001
            ok = False
            health["sources"][name] = {"ok": False, "error": str(e)[:120]}
        (report.info if ok else report.warn)(f"소스 점검 {name}: {'정상' if ok else '응답 없음 — ' + str(health['sources'][name])}")
    write_json(config.DATA_DIR / "health.json", health)
    aggregate.build(config.DATA_DIR, _now(), report, validate=validate, run_kind="monthly")
    report.deploy = True


# ---------- main ----------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline")
    ap.add_argument("command", choices=["update", "advance", "monthly", "aggregate", "collect", "status"])
    ap.add_argument("--years", default="")
    ap.add_argument("--venues", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="판별 캐시를 무시하고 전체 재판별")
    ap.add_argument("--no-validate", action="store_true", help="배포 전 편수 검증 건너뛰기")
    ap.add_argument("--wait-minutes", type=float, default=270)
    ap.add_argument("--log", default="INFO")
    args = ap.parse_args(argv)
    setup_logging(args.log)
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    report = Report(f"파이프라인 · {args.command}")
    st = state.load()
    try:
        if args.command == "status":
            print(read_json(state.path(), state.DEFAULT))
            return 0
        now = _now()
        years = _parse_list(args.years, int)
        vids = _parse_list(args.venues)
        if args.command == "collect":
            pairs = scope_pairs(years, vids, now)
            cmd_collect(pairs, report)
        elif args.command == "update":
            if st["phase"] != "idle":
                report.warn(f"이전 실행이 '{st['phase']}' 단계에 있어 먼저 이어서 처리합니다")
                cmd_advance(st, report, args.wait_minutes, not args.no_validate)
                if st["phase"] != "idle":
                    report.warn("이전 Batch 가 아직 끝나지 않아 새 수집을 시작하지 않습니다. 다음 advance 실행이 이어받습니다")
                    report.save()
                    return 0
            pairs = scope_pairs(years, vids, now)
            report.info(f"범위: {len(pairs)}개 학회·연도 " + (f"(연도 {years})" if years else "(집계 미완료 전체)"))
            abstracts = cmd_collect(pairs, report)
            submitted = cmd_judge_submit(pairs, abstracts, st, report, args.force, args.dry_run)
            if submitted:
                cmd_advance(st, report, args.wait_minutes, not args.no_validate)
            elif not args.dry_run:
                step_finish(st, report, not args.no_validate)
        elif args.command == "advance":
            cmd_advance(st, report, args.wait_minutes, not args.no_validate)
        elif args.command == "monthly":
            cmd_monthly(report, not args.no_validate)
        elif args.command == "aggregate":
            aggregate.build(config.DATA_DIR, now, report, validate=not args.no_validate, run_kind="manual")
            report.deploy = True
    except aggregate.ValidationError as e:
        report.warn(str(e))
        write_error(str(e))
        report.save()
        return 2
    except SystemExit as e:
        if e.code in (0, None):
            return 0
        write_error(str(e.code))
        report.warn(str(e.code))
        report.save()
        return e.code if isinstance(e.code, int) else 1
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc()
        log.error(tb)
        write_error(f"{e.__class__.__name__}: {e}\n\n{tb}")
        report.warn(f"실패: {e.__class__.__name__}: {str(e)[:200]}")
        report.save()
        return 1
    report.save()
    print(report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
