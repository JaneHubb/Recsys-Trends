"""CLI entry point.

  python -m pipeline.run update   [--years 2025,2026] [--venues KDD,SIGIR] [--dry-run] [--force] [--wait-minutes N]
  python -m pipeline.run advance  [--wait-minutes N]      pick up an in-flight Batch and carry it to the end
  python -m pipeline.run monthly                          heartbeat · source health check · re-aggregate
  python -m pipeline.run aggregate [--no-validate]        aggregation only
  python -m pipeline.run collect  [--years ...] [--venues ...]   collection only (abstracts go to work/)
  python -m pipeline.run status                           print the state machine
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

SONNET_PER_PAPER_USD = cost.estimate_usd(config.REJUDGE_MODEL, 1300, 80)   # estimate for one re-judged paper


def _now() -> datetime:
    return datetime.now(timezone.utc)


now_iso_dt = _now


def _parse_list(s: str | None, cast=str) -> list:
    return [cast(x.strip()) for x in (s or "").split(",") if x.strip()]


# ---------- scope ----------

def scope_pairs(years: list[int], vids: list[str], now: datetime) -> list[tuple[str, int]]:
    """Without an explicit scope: every venue-year that is not yet 'done' (first run = backfill)."""
    cfg = venues.load()
    all_v = venues.venue_ids()
    vids = vids or all_v
    unknown = [v for v in vids if v not in cfg["venues"]]
    if unknown:
        raise SystemExit(f"Venues not defined in venues.yaml: {unknown}")
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
                continue   # a conference that has not happened yet is not on DBLP; OpenReview venues publish decisions first, so try them
            pairs.append((v, y))
    return pairs


# ---------- collection ----------

def cmd_collect(pairs: list[tuple[str, int]], report: Report) -> dict[str, str]:
    """Write raw files; keep abstracts in work/abstracts.jsonl. Returns the abstracts dict.
    Raises if every venue-year in scope failed (source outage → Issue)."""
    cfg = venues.load()
    dblp_http = Http(min_interval=1.2)
    or_http = Http(min_interval=0.5)
    abstracts: dict[str, str] = {}
    n_fail = 0
    report.section("Collection")
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
                elif papers:   # RecSys: only arXiv id and citation count, for links
                    meta, _ = enrich_s2.enrich(papers, report)
                    for p in papers:
                        p.update(meta.get(p["id"], {}))
                raw = {"venue": vid, "year": y, "source": "dblp", "collected": now_iso(),
                       "volumes": res["volumes"], "sections": res["sections"], "papers": papers}
            else:
                orcfgs = venues.openreview_configs(vcfg, y)
                if not orcfgs:
                    report.warn(f"{vid} {y}: no openreview configuration")
                    continue
                res = collect_openreview.collect(or_http, vcfg, y, orcfgs, report)
                papers = res["papers"]
                abstracts.update(res["abstracts"])
                raw = {"venue": vid, "year": y, "source": "openreview", "collected": now_iso(),
                       "volumes": res["volumes"], "sections": True, "papers": papers}
                if not papers and vcfg.get("dblp", {}).get("tocs") and aggregate.month_passed(now_iso_dt(), y, vcfg["month"]):
                    # OpenReview returned nothing and the conference is over: fall back to DBLP (guards against API changes)
                    res = collect_dblp.collect(dblp_http, vcfg, y, report)
                    papers = res["papers"]
                    if papers:
                        report.warn(f"{vid} {y}: OpenReview returned 0 papers → collected {len(papers)} from DBLP instead (no oral/spotlight badges)")
                        meta, abs_ = enrich_s2.enrich(papers, report)
                        for p in papers:
                            p.update(meta.get(p["id"], {}))
                        abstracts.update(abs_)
                        raw = {"venue": vid, "year": y, "source": "dblp", "collected": now_iso(),
                               "volumes": res["volumes"], "sections": res["sections"], "papers": papers}
        except Exception as e:  # noqa: BLE001
            n_fail += 1
            report.warn(f"{vid} {y} collection failed: {e.__class__.__name__}: {str(e)[:160]}")
            log.debug(traceback.format_exc())
            continue
        if not papers:
            report.info(f"{vid} {y}: 0 papers (not available yet)")
            continue
        counted = sum(1 for p in papers if p["track"] in venues.counted_tracks())
        tracks = {}
        for p in papers:
            tracks[p["track"]] = tracks.get(p["track"], 0) + 1
        report.info(f"{vid} {y}: {len(papers)} collected, {counted} counted · " + ", ".join(f"{k} {n}" for k, n in sorted(tracks.items())))
        write_json(config.DATA_DIR / "raw" / f"{vid}-{y}.json", raw, indent=0)
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(config.WORK_DIR / "abstracts.jsonl", ({"id": k, "abstract": v} for k, v in abstracts.items()))
    if pairs and n_fail == len(pairs):
        raise RuntimeError(f"Collection failed for all {len(pairs)} venue-years in scope. Check DBLP / OpenReview connectivity (see warnings in the report)")
    return abstracts


def load_work_abstracts() -> dict[str, str]:
    return {r["id"]: r["abstract"] for r in read_jsonl(config.WORK_DIR / "abstracts.jsonl")}


def raw_index(pairs: list[tuple[str, int]] | None = None) -> dict[str, dict]:
    """id → raw record (counted tracks only)"""
    counted = set(venues.counted_tracks())
    out: dict[str, dict] = {}
    for (vid, y), raw in aggregate.load_raw(config.DATA_DIR).items():
        if pairs is not None and (vid, y) not in pairs:
            continue
        for p in raw.get("papers", []):
            if p.get("track") in counted:
                out[p["id"]] = p
    return out


# ---------- judge submission ----------

def cmd_judge_submit(pairs: list[tuple[str, int]], abstracts: dict[str, str], st: dict, report: Report,
                     force: bool, dry_run: bool) -> bool:
    cfg = venues.load()
    cache = {} if force else judge.load_cache(config.DATA_DIR / "llm" / "judge.jsonl", config.JUDGE_MODEL, config.PROMPT_VERSION)
    idx = raw_index(pairs)
    todo = [p for p in idx.values() if not cfg["venues"][p["venue"]].get("all_core") and p["id"] not in cache]
    todo.sort(key=lambda p: (-p["year"], p["venue"], p["id"]))
    system, template = judge.load_prompt()
    report.section("LLM classification")
    if not todo:
        report.info("Nothing to classify (all cached)")
        return False
    # Budget: Haiku estimate + Sonnet reserve (3% low-confidence + quality sample) ≤ MAX_RUN_USD.
    # If it does not fit, take the newest years first and defer the rest.
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
    report.info(f"{len(todo):,} papers to classify ({n_abs:,} with abstract) · this run {len(selected):,}, estimated ${running:.2f} "
                f"(cap ${config.MAX_RUN_USD} including ${reserve:.2f} Sonnet reserve)")
    if deferred:
        report.warn(f"Budget cap: {deferred:,} papers deferred to the next run (newest years first). Run the workflow manually next month to continue")
    if dry_run:
        report.info("dry-run: no Batch submitted")
        return False
    cl = judge.client()
    judge.preflight(cl, [config.JUDGE_MODEL, config.REJUDGE_MODEL])
    reqs = [judge.build_request(p, abstracts.get(p["id"]), config.JUDGE_MODEL, system, template) for p in selected]
    extra = {judge.safe_cid(p["id"]): {"id": p["id"], "kw": judge.kw_hit(p["title"], abstracts.get(p["id"])), "abs": p["id"] in abstracts} for p in selected}
    write_json(config.STATE_DIR / "pending_extra.json", extra)
    st.update({"phase": "judge_submitted", "run_id": _now().strftime("%Y%m%d-%H%M"), "started": now_iso(),
               "batches": [], "estimate_usd": round(running, 3), "deferred": deferred})
    st["scope"] = {"years": sorted({y for _, y in pairs}), "venues": sorted({v for v, _ in pairs}), "force": force}

    def on_batch(bid: str) -> None:          # save after every chunk so a mid-way failure never orphans a submitted Batch
        st["batches"].append(bid)
        state.save(st)

    judge.submit(cl, reqs, on_batch=on_batch)
    state.note(st, f"Haiku submitted {len(selected)} papers in {len(st['batches'])} batch(es)")
    state.save(st)
    return True


# ---------- advance ----------

def _wait_or_break(deadline: float, report: Report, what: str) -> bool:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        report.info(f"{what} still processing — wait window is over, exiting. The next advance run will pick it up")
        return False
    time.sleep(min(300, max(30, remaining)))
    return True


def _check_batch_errors(rows: list[dict], errors: list[dict], st: dict, report: Report, label: str) -> None:
    """Record error rows; abort if everything failed (state is kept so advance can re-collect after the cause is fixed)."""
    if errors:
        append_jsonl(config.DATA_DIR / "llm" / "errors.jsonl", [{**e, "ts": now_iso(), "run": st.get("run_id")} for e in errors])
    if errors and not rows:
        raise RuntimeError(f"Every {label} Batch result failed (e.g. {errors[0].get('type')} {errors[0].get('error')}). "
                           f"Check the model ids ({config.JUDGE_MODEL}, {config.REJUDGE_MODEL}) and the API key")
    if errors and len(errors) > 0.05 * (len(rows) + len(errors)):
        report.warn(f"{label} Batch: {len(errors)} errors ({len(errors) / (len(rows) + len(errors)):.0%}) — see data/llm/errors.jsonl. Failed papers are retried on the next run")


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
    report.info(f"Haiku results: {len(rows):,} papers (core {by.get('core', 0):,} · related {by.get('related', 0):,} · no {by.get('no', 0):,}) · "
                f"errors {len(errors)} · actual cost ${spent:.2f}")
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
    state.note(st, f"Haiku done: {len(rows)} papers, {len(low)} low-confidence, {len(sample)} quality sample")
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
        report.warn(f"Budget exhausted: re-judging only {n_afford} of {len(low)} low-confidence papers (newest years first)")
        low = low[:n_afford]
    n_afford -= len(low)
    sample = [i for i in st.get("quality_ids", []) if i in idx][:max(0, n_afford)]
    if len(sample) < len(st.get("quality_ids", [])):
        report.warn(f"Quality sample reduced to {len(sample)} papers because of the budget")
    st["quality_ids"] = sample
    ids = list(dict.fromkeys(low + sample))
    if not ids:
        st["phase"] = "rejudged"
        state.note(st, "Nothing to re-judge")
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
    state.note(st, f"Sonnet submitted {len(ids)} papers ({len(low)} low-confidence + {len(sample)} sample), estimated ${usd:.2f}")
    report.info(f"Sonnet re-judge submitted: {len(low):,} low-confidence + {len(sample):,} quality sample, estimated ${usd:.2f}")
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
    # Quality (internal only): Haiku ↔ Sonnet agreement on the sample
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
    report.info(f"Sonnet results: {len(rows):,} papers · errors {len(errors)} · cost ${spent:.2f} · "
                f"sample agreement {q['agreement'] if q['agreement'] is not None else '-'} (data/quality.json, internal)")
    st["phase"] = "rejudged"
    state.note(st, f"Sonnet done: {len(rows)} papers, agreement {q['agreement']}")
    state.save(st)


def step_finish(st: dict, report: Report, validate: bool, run_kind: str = "update") -> None:
    aggregate.build(config.DATA_DIR, _now(), report, validate=validate, run_kind=run_kind)
    meta = read_json(config.DATA_DIR / "meta.json", {}) or {}
    meta["last_update"] = _now().date().isoformat()
    meta["last_run_id"] = st.get("run_id")
    meta["last_spent_usd"] = st.get("spent_usd", 0.0)
    write_json(config.DATA_DIR / "meta.json", meta)
    report.info(f"LLM cost this run (actual) ${st.get('spent_usd', 0.0):.2f} · deferred papers {st.get('deferred', 0)}")
    try:
        (config.STATE_DIR / "pending_extra.json").unlink()
    except FileNotFoundError:
        pass
    new = state.reset(st)
    state.note(new, f"Finished run {st.get('run_id')} · cost ${st.get('spent_usd', 0.0):.2f}")
    state.save(new)
    st.clear()
    st.update(new)
    report.deploy = True


def cmd_advance(st: dict, report: Report, wait_minutes: float, validate: bool) -> None:
    deadline = time.monotonic() + wait_minutes * 60
    cl = None
    report.section("Advance")
    while True:
        ph = st["phase"]
        if ph == "idle":
            report.info("Nothing in progress")
            return
        if ph in ("judge_submitted", "rejudge_submitted"):
            cl = cl or judge.client()
            ids = st["batches"] if ph == "judge_submitted" else st["rejudge_batches"]
            s = judge.status(cl, ids)
            if not s["ended"]:
                c = s["counts"]
                log.info("Batch processing: %d succeeded / %d processing", c["succeeded"], c["processing"])
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
        raise RuntimeError(f"Unknown phase: {ph}")


# ---------- monthly ----------

def cmd_monthly(report: Report, validate: bool) -> None:
    report.section("Monthly check")
    write_json(config.DATA_DIR / "heartbeat.json", {"ts": now_iso(), "note": "Committed monthly so scheduled workflows are not disabled after 60 days of inactivity"})
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
        (report.info if ok else report.warn)(f"Source check {name}: {'OK' if ok else 'no response — ' + str(health['sources'][name])}")
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
    ap.add_argument("--force", action="store_true", help="ignore the classification cache and re-judge everything")
    ap.add_argument("--no-validate", action="store_true", help="skip the pre-deploy count validation")
    ap.add_argument("--wait-minutes", type=float, default=270)
    ap.add_argument("--log", default="INFO")
    args = ap.parse_args(argv)
    setup_logging(args.log)
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    report = Report(f"Pipeline · {args.command}")
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
                report.warn(f"A previous run is at phase '{st['phase']}'; finishing it first")
                cmd_advance(st, report, args.wait_minutes, not args.no_validate)
                if st["phase"] != "idle":
                    report.warn("The previous Batch has not finished; not starting a new collection. The next advance run will pick it up")
                    report.save()
                    return 0
            pairs = scope_pairs(years, vids, now)
            report.info(f"Scope: {len(pairs)} venue-years " + (f"(years {years})" if years else "(everything not yet complete)"))
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
        report.warn(f"Failed: {e.__class__.__name__}: {str(e)[:200]}")
        report.save()
        return 1
    report.save()
    print(report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
