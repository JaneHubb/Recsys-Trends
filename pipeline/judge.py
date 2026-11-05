"""추천 논문 판별: Claude Batch API.

- 요청 1건 = 논문 1편 (Haiku 5.5 는 요청이 10만 토큰을 넘으면 단가가 5배라 묶지 않는다)
- 시스템 프롬프트에 cache_control 을 붙여 두지만, 캐시 최소 길이(Haiku 2048토큰)보다 짧으면 적용되지 않는다 (무해)
- 결과: data/llm/judge.jsonl (Haiku), data/llm/rejudge.jsonl (Sonnet). 캐시 키 = (id, prompt_version, model)
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import config, cost
from .utils import log

LABELS = ("core", "related", "no")
_KW = re.compile(config.KEYWORD_PATTERN, re.I)


def load_prompt(version: str = config.PROMPT_VERSION) -> tuple[str, str]:
    text = (config.PROMPTS_DIR / f"judge_{version}.md").read_text(encoding="utf-8")
    sys_part = text.split("## system", 1)[1].split("## user", 1)[0].strip()
    user_part = text.split("## user", 1)[1].strip()
    return sys_part, user_part


def kw_hit(title: str, abstract: str | None) -> bool:
    return bool(_KW.search(f"{title}\n{abstract or ''}"))


def build_user(template: str, paper: dict, abstract: str | None) -> str:
    ab = (abstract or "").strip()
    if len(ab) > config.ABSTRACT_MAX_CHARS:
        ab = ab[: config.ABSTRACT_MAX_CHARS] + " …"
    return template.format(venue=paper["venue"], year=paper["year"], title=paper["title"], abstract=ab or "(not available)")


def safe_cid(pid: str) -> str:
    """Batch custom_id 는 [A-Za-z0-9_-]{1,64} 만 허용된다. 원래 id 는 pending_extra.json 에 함께 저장한다"""
    san = re.sub(r"[^A-Za-z0-9_-]", "_", pid)[:52]
    return f"{san}_{hashlib.sha1(pid.encode()).hexdigest()[:8]}"


def build_request(paper: dict, abstract: str | None, model: str, system: str, template: str) -> dict:
    return {
        "custom_id": safe_cid(paper["id"]),
        "params": {
            "model": model,
            "max_tokens": config.JUDGE_MAX_TOKENS,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": build_user(template, paper, abstract)}],
        },
    }


def estimate(papers: list[dict], abstracts: dict[str, str], model: str, system: str, template: str) -> tuple[float, int, int]:
    """(USD, 입력 토큰, 출력 토큰). 시스템 프롬프트는 캐시 최소 길이(Haiku 2048토큰)보다 짧아 캐시되지 않는다고 보고 전부 센다"""
    sys_tok = cost.tokens(system)
    tin = tout = 0
    for p in papers:
        tin += cost.tokens(build_user(template, p, abstracts.get(p["id"]))) + sys_tok + 20
        tout += 70
    return cost.estimate_usd(model, tin, tout), tin, tout


def parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        try:
            obj = json.loads(m.group(0).replace("\n", " ").replace("'", '"'))
        except json.JSONDecodeError:
            return None
    label = str(obj.get("label", "")).strip().lower()
    if label not in LABELS:
        return None
    conf = str(obj.get("confidence", "low")).strip().lower()
    return {
        "label": label,
        "confidence": "high" if conf == "high" else "low",
        "reason": str(obj.get("reason", ""))[:80],
    }


# ---------- Batch API ----------

def client():
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY 가 설정되지 않았습니다 (Settings → Secrets → Actions)")
    from anthropic import Anthropic
    return Anthropic(max_retries=5)


def preflight(cl, models: list[str]) -> None:
    """모델 ID 가 틀리면 5시간 뒤가 아니라 지금 실패하도록 미리 확인한다"""
    for m in models:
        try:
            cl.models.retrieve(m)
        except Exception as e:  # noqa: BLE001
            name = e.__class__.__name__
            if "NotFound" in name or "404" in str(e):
                raise SystemExit(f"모델 '{m}' 을 찾을 수 없습니다. pipeline/config.py 의 JUDGE_MODEL / REJUDGE_MODEL 을 확인하세요 ({e})")
            log.warning("모델 확인 생략 (%s: %s)", name, str(e)[:100])


def submit(cl, requests_: list[dict], on_batch=None) -> list[str]:
    ids: list[str] = []
    for i in range(0, len(requests_), config.BATCH_CHUNK):
        chunk = requests_[i:i + config.BATCH_CHUNK]
        b = cl.messages.batches.create(requests=chunk)
        log.info("Batch 제출 %s (%d건)", b.id, len(chunk))
        ids.append(b.id)
        if on_batch:
            on_batch(b.id)
    return ids


def status(cl, batch_ids: list[str]) -> dict:
    """{'ended': bool, 'counts': {...}}"""
    ended = True
    counts = {"processing": 0, "succeeded": 0, "errored": 0, "canceled": 0, "expired": 0}
    for bid in batch_ids:
        b = cl.messages.batches.retrieve(bid)
        if b.processing_status != "ended":
            ended = False
        rc = b.request_counts
        for k in counts:
            counts[k] += getattr(rc, k, 0) or 0
    return {"ended": ended, "counts": counts}


def collect_results(cl, batch_ids: list[str], paper_index: dict[str, dict], model: str, prompt_version: str,
                    extra: dict[str, dict] | None = None) -> tuple[list[dict], list[dict], dict]:
    """(판정 행, 오류 행, usage 합계)"""
    rows: list[dict] = []
    errors: list[dict] = []
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    for bid in batch_ids:
        for res in cl.messages.batches.results(bid):
            cid = res.custom_id
            if res.result.type != "succeeded":
                err = getattr(res.result, "error", None)
                errors.append({"id": ((extra or {}).get(cid) or {}).get("id", cid), "type": res.result.type, "error": str(err)[:200] if err else None, "batch": bid})
                continue
            msg = res.result.message
            u = msg.usage
            usage["input"] += getattr(u, "input_tokens", 0) or 0
            usage["output"] += getattr(u, "output_tokens", 0) or 0
            usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
            usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
            text = "".join(getattr(c, "text", "") for c in msg.content)
            parsed = parse(text)
            if not parsed:
                errors.append({"id": ((extra or {}).get(cid) or {}).get("id", cid), "type": "unparsable", "error": text[:200], "batch": bid})
                continue
            ex = dict((extra or {}).get(cid) or {})
            real_id = ex.pop("id", cid)
            rows.append({"id": real_id, **parsed, "model": model, "pv": prompt_version, **ex})
    return rows, errors, usage


def load_cache(path: Path, model: str, prompt_version: str) -> dict[str, dict]:
    """같은 (모델, 프롬프트 버전)의 최신 판정만"""
    from .utils import read_jsonl
    out: dict[str, dict] = {}
    for r in read_jsonl(path):
        if r.get("model") == model and r.get("pv") == prompt_version and r.get("label") in LABELS:
            out[r["id"]] = r
    return out
