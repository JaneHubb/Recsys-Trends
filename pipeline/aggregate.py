"""집계: 원본(raw) + 판정(llm) → status.json, summary.json, papers/*.json, changelog.json

모든 계산을 메모리에서 끝낸 뒤 검증을 통과해야 파일을 쓴다 (이상한 데이터가 배포되지 않게).
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from pathlib import Path

from . import config, judge, venues
from .utils import Report, now_iso, read_json, read_jsonl, write_json

PAPER_FIELDS = ("id", "title", "authors", "track", "doi", "url", "arxiv", "cites", "badge", "openreview")


class ValidationError(Exception):
    pass


# ---------- 입력 ----------

def load_raw(data_dir: Path) -> dict[tuple[str, int], dict]:
    out: dict[tuple[str, int], dict] = {}
    for p in sorted((data_dir / "raw").glob("*.json")):
        raw = read_json(p)
        if raw and "venue" in raw:
            out[(raw["venue"], int(raw["year"]))] = raw
    return out


def load_judgments(data_dir: Path) -> dict[str, dict]:
    """Haiku 판정 위에 Sonnet 재판정을 덮는다. 현재 프롬프트 버전만 쓴다"""
    out: dict[str, dict] = {}
    for r in read_jsonl(data_dir / "llm" / "judge.jsonl"):
        if r.get("pv") == config.PROMPT_VERSION and r.get("model") == config.JUDGE_MODEL and r.get("label") in judge.LABELS:
            out[r["id"]] = r
    for r in read_jsonl(data_dir / "llm" / "rejudge.jsonl"):
        if r.get("pv") == config.PROMPT_VERSION and r.get("model") == config.REJUDGE_MODEL and r.get("label") in judge.LABELS:
            prev = out.get(r["id"], {})
            out[r["id"]] = {**prev, **r, "rejudged": True}
    return out


# ---------- 일정 ----------

def next_update(now: datetime) -> str:
    cands = []
    for y in (now.year, now.year + 1):
        for m, d in config.UPDATE_DATES:
            cands.append(datetime(y, m, d, tzinfo=timezone.utc))
    for c in sorted(cands):
        if c.date() > now.date():
            return c.date().isoformat()
    return cands[-1].date().isoformat()


def month_passed(now: datetime, year: int, month: int) -> bool:
    """개최월 + 1개월이 지났는가 (예: 8월 학회 → 10월 1일부터 True)"""
    m, y = month + 2, year
    if m > 12:
        m -= 12
        y += 1
    return (now.year, now.month) >= (y, m)


# ---------- 집계 상태 ----------

def compute_state(vcfg: dict, year: int, raw: dict | None, counted: int, pending: int, prev_counted: int | None, now: datetime) -> tuple[str, str]:
    """('done'|'part'|'planned', 이유)"""
    if year > now.year:
        return "planned", "미래 연도"
    if not raw or counted == 0:
        if year == now.year and not month_passed(now, year, vcfg["month"]):
            return "planned", "개최 전"
        return "planned", "목록 없음"
    if pending > max(5, counted * 0.02):
        return "part", f"판별 대기 {pending}편"
    if vcfg.get("complete_rule", "date") == "date" and not month_passed(now, year, vcfg["month"]):
        return "part", "개최 후 1개월 미경과"
    exp = venues.expected_volumes(vcfg, year) if venues.source_for(vcfg, year) == "dblp" else 1
    found = len(raw.get("volumes") or [])
    if found < exp and not (prev_counted and counted >= 0.9 * prev_counted):
        return "part", f"볼륨 {found}/{exp}"
    if prev_counted and counted < 0.7 * prev_counted:
        return "part", f"전년의 {counted / prev_counted:.0%}뿐"
    return "done", "완료"


# ---------- 본체 ----------

def build(data_dir: Path, now: datetime, report: Report, validate: bool = True, run_kind: str = "update") -> dict:
    cfg = venues.load()
    vlist = [cfg["venues"][v] for v in venues.venue_ids()]
    counted_tracks = set(venues.counted_tracks())
    years = list(range(venues.start_year(), now.year + 1))
    raws = load_raw(data_dir)
    judg = load_judgments(data_dir)
    old_status = read_json(data_dir / "status.json", {}) or {}
    meta = read_json(data_dir / "meta.json", {}) or {}

    status: dict[str, dict[str, dict]] = {}
    papers_out: dict[tuple[str, int], dict] = {}

    for vcfg in vlist:
        vid = vcfg["id"]
        status[vid] = {}
        prev_counted = None
        for y in years:
            raw = raws.get((vid, y))
            papers = [p for p in (raw or {}).get("papers", []) if p.get("track") in counted_tracks]
            demo = [p for p in (raw or {}).get("papers", []) if p.get("track") == "demo"]
            core: list[dict] = []
            related: list[dict] = []
            border: list[dict] = []
            pending = 0
            for p in papers:
                if vcfg.get("all_core"):
                    core.append({**p, "label": "core"})
                    continue
                j = judg.get(p["id"])
                if not j:
                    pending += 1
                    continue
                if j["label"] == "core":
                    core.append({**p, "label": "core", "reason": j.get("reason"), "rejudged": j.get("rejudged", False)})
                elif j["label"] == "related":
                    related.append({**p, "label": "related", "reason": j.get("reason"), "rejudged": j.get("rejudged", False)})
                elif j.get("kw"):
                    border.append({**p, "reason": j.get("reason")})
            st, why = compute_state(vcfg, y, raw, len(papers), pending, prev_counted, now)
            status[vid][str(y)] = {
                "state": st,
                "why": why,
                "total": len(papers),
                "core": len(core),
                "related": len(related),
                "border": len(border),
                "pending": pending,
                "demo": len(demo),
                "volumes": (raw or {}).get("volumes", []),
                "sections": bool((raw or {}).get("sections")),
                "collected": (raw or {}).get("collected"),
            }
            if raw:
                papers_out[(vid, y)] = {
                    "venue": vid, "year": y, "state": st,
                    "papers": [_slim(p) for p in core + related],
                    "border": [_slim(p, border=True) for p in border],
                    "demo": [_slim(p) for p in demo] if demo else [],
                }
            if st == "done":
                prev_counted = len(papers)
            elif len(papers):
                prev_counted = prev_counted  # 미완료 연도는 전년 기준으로 삼지 않는다

    # 기준 연도
    n_venues = len(vlist)
    min_done = max(1, round(n_venues * 0.75))
    done_by_year = {y: [v["id"] for v in vlist if status[v["id"]][str(y)]["state"] == "done"] for y in years}
    basis_year = max((y for y in years if len(done_by_year[y]) >= min_done), default=None)
    if basis_year is None:
        basis_year = max((y for y in years if done_by_year[y]), default=None)
    basis = {"year": basis_year, "done": len(done_by_year[basis_year]) if basis_year else 0, "total": n_venues,
             "kind": None, "missing": []}
    if basis_year:
        basis["kind"] = "final" if basis["done"] == n_venues else "provisional"
        basis["missing"] = [v["id"] for v in vlist if v["id"] not in done_by_year[basis_year]]

    overview = _overview(vlist, status, basis_year, done_by_year)
    in_progress = [{"year": y, "done": len(done_by_year[y]), "total": n_venues,
                    "venues": {v["id"]: status[v["id"]][str(y)]["state"] for v in vlist}}
                   for y in years if basis_year and y > basis_year]
    sentences = _sentences(basis, overview, now)

    new_status = {"generated": now_iso(), "venues": status}
    if validate:
        _validate(old_status.get("venues", {}), status)

    changelog = read_json(data_dir / "changelog.json", []) or []
    changelog.append(_changelog_entry(old_status.get("venues", {}), status, run_kind, now))
    changelog = changelog[-40:]

    summary = {
        "generated": now_iso(),
        "data_date": meta.get("last_update"),
        "next_update": next_update(now),
        "prompt_version": config.PROMPT_VERSION,
        "judge_model": config.JUDGE_MODEL,
        "years": years,
        "venues": [{"id": v["id"], "name": v.get("name", v["id"]), "color": v.get("color", 0), "all_core": bool(v.get("all_core")), "month": v["month"]} for v in vlist],
        "counted_tracks": sorted(counted_tracks),
        "basis": basis,
        "in_progress": in_progress,
        "overview": overview,
        "sentences": sentences,
        "counts": {v["id"]: {y: {k: status[v["id"]][y][k] for k in ("state", "why", "total", "core", "related", "border", "pending")} for y in status[v["id"]]} for v in vlist},
        "changelog": changelog[-6:][::-1],
    }

    # ---------- 쓰기 ----------
    write_json(data_dir / "status.json", new_status)
    write_json(data_dir / "summary.json", summary)
    write_json(data_dir / "changelog.json", changelog)
    pdir = data_dir / "papers"
    pdir.mkdir(parents=True, exist_ok=True)
    for (vid, y), obj in papers_out.items():
        write_json(pdir / f"{vid}-{y}.json", obj, indent=0)
    report.section("집계")
    report.info(f"기준 연도 {basis_year} ({basis['kind']}, {basis['done']}/{n_venues}) · 추천 논문(core) {overview.get('core', 0):,}편")
    for v in vlist:
        row = status[v["id"]]
        parts = [f"{y}:{row[y]['state'][0]}{row[y]['core']}" for y in row if row[y]["state"] != "planned"]
        report.info(f"{v['id']}: " + " ".join(parts))
    return summary


def _slim(p: dict, border: bool = False) -> dict:
    out = {k: p.get(k) for k in PAPER_FIELDS if p.get(k) is not None}
    out["authors"] = p.get("authors", [])
    if not border:
        out["label"] = p.get("label")
        if p.get("rejudged"):
            out["rejudged"] = True
    if p.get("reason"):
        out["reason"] = p["reason"]
    return out


def _overview(vlist: list[dict], status: dict, basis_year: int | None, done_by_year: dict) -> dict:
    if not basis_year:
        return {}
    y, py = str(basis_year), str(basis_year - 1)
    done_now = set(done_by_year[basis_year])
    done_prev = set(done_by_year.get(basis_year - 1, []))
    both = sorted(done_now & done_prev)
    core_now = sum(status[v][y]["core"] for v in done_now)
    rel_now = sum(status[v][y]["related"] for v in done_now)
    cmp_now = sum(status[v][y]["core"] for v in both)
    cmp_prev = sum(status[v][py]["core"] for v in both) if both else 0

    def ratios(year: str, vs: set) -> list[float]:
        out = []
        for v in vlist:
            if v["id"] in vs and not v.get("all_core") and status[v["id"]][year]["total"]:
                out.append(status[v["id"]][year]["core"] / status[v["id"]][year]["total"])
        return out

    r_now, r_prev = ratios(y, done_now), ratios(py, done_prev & done_now)
    return {
        "core": core_now,
        "core_related": core_now + rel_now,
        "compare_venues": both,
        "compare_core": cmp_now,
        "compare_prev_core": cmp_prev,
        "delta": (cmp_now / cmp_prev - 1) if cmp_prev else None,
        "ratio_median": statistics.median(r_now) if r_now else None,
        "ratio_median_prev": statistics.median(r_prev) if r_prev else None,
        "ratio_venues": len(r_now),
    }


def _sentences(basis: dict, ov: dict, now: datetime) -> list[str]:
    out: list[str] = []
    if not basis.get("year") or not ov:
        return ["아직 집계가 끝난 학회가 없습니다. 첫 업데이트가 끝나면 요약이 표시됩니다."]
    y = basis["year"]
    if ov.get("delta") is not None and ov.get("compare_venues"):
        d = ov["delta"]
        out.append(f"{y}년 추천 논문은 비교 가능한 {len(ov['compare_venues'])}개 학회 기준 "
                   f"{ov['compare_prev_core']:,}편 → {ov['compare_core']:,}편 ({'+' if d >= 0 else ''}{d * 100:.1f}%).")
    if ov.get("ratio_median") is not None and ov.get("ratio_median_prev") is not None:
        r0, r1 = ov["ratio_median_prev"] * 100, ov["ratio_median"] * 100
        trend = "제자리" if abs(r1 - r0) < 1 else ("올랐습니다" if r1 > r0 else "내렸습니다")
        out.append(f"학회 내 추천 비율(중앙값)은 {r0:.1f}% → {r1:.1f}%로 {trend}.")
    if basis.get("kind") == "provisional" and basis.get("missing"):
        out.append(f"{', '.join(basis['missing'])} 집계 중 · {next_update(now)} 업데이트에 반영 예정.")
    return out


def _validate(old: dict, new: dict) -> None:
    """완료된 학회·연도의 편수가 20% 넘게 바뀌거나, 전체가 50% 넘게 줄면 중단"""
    problems = []
    old_total = new_total = 0
    for vid, years in old.items():
        for y, o in years.items():
            n = new.get(vid, {}).get(y)
            if not n:
                continue
            old_total += o.get("total", 0)
            new_total += n.get("total", 0)
            if o.get("state") == "done" and o.get("total", 0) >= 20:
                change = n["total"] / o["total"] - 1
                if abs(change) > 0.2:
                    problems.append(f"{vid} {y}: 전체 편수 {o['total']} → {n['total']} ({change:+.0%})")
    if old_total >= 100 and new_total < 0.5 * old_total:
        problems.append(f"전체 편수 {old_total} → {new_total}")
    if problems:
        raise ValidationError("배포 중단 — 이전 집계와 크게 다릅니다:\n" + "\n".join(problems) +
                              "\n원인을 확인한 뒤 수동 실행에서 '검증 건너뛰기'로 다시 돌릴 수 있습니다.")


def _changelog_entry(old: dict, new: dict, kind: str, now: datetime) -> dict:
    def tot(s: dict, key: str) -> int:
        return sum(y.get(key, 0) for v in s.values() for y in v.values())
    newly_done = [f"{v} {y}" for v, ys in new.items() for y, r in ys.items()
                  if r["state"] == "done" and old.get(v, {}).get(y, {}).get("state") != "done"]
    return {
        "date": now.date().isoformat(),
        "kind": kind,
        "core": tot(new, "core"),
        "core_delta": tot(new, "core") - tot(old, "core"),
        "border_delta": tot(new, "border") - tot(old, "border"),
        "newly_done": newly_done,
    }
