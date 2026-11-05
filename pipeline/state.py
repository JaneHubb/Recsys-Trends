"""파이프라인 상태 머신 (state/pipeline.json).

phase: idle → judge_submitted → judged → rejudge_submitted → rejudged → (aggregate) → idle
Batch 는 최대 24시간, Actions 작업은 최대 6시간이라 실행이 끊겨도 다음 실행이 이어받는다.
"""
from __future__ import annotations

from . import config
from .utils import now_iso, read_json, write_json

PHASES = ("idle", "judge_submitted", "judged", "rejudge_submitted", "rejudged")

DEFAULT = {
    "phase": "idle",
    "run_id": None,
    "started": None,
    "updated": None,
    "scope": {"years": [], "venues": [], "force": False},
    "batches": [],
    "rejudge_batches": [],
    "rejudge_ids": [],
    "quality_ids": [],
    "usage": {},            # {model: {input, output, cache_read, cache_write}}
    "spent_usd": 0.0,
    "estimate_usd": 0.0,
    "deferred": 0,          # 예산 때문에 다음 실행으로 미룬 논문 수
    "notes": [],
}


def path():
    return config.STATE_DIR / "pipeline.json"


def load() -> dict:
    st = read_json(path(), None)
    if not st:
        return dict(DEFAULT)
    for k, v in DEFAULT.items():
        st.setdefault(k, v if not isinstance(v, (dict, list)) else type(v)(v))
    return st


def save(st: dict) -> None:
    st["updated"] = now_iso()
    write_json(path(), st)


def reset(st: dict) -> dict:
    keep_notes = st.get("notes", [])[-10:]
    new = dict(DEFAULT)
    new["scope"] = {"years": [], "venues": [], "force": False}
    new["batches"], new["rejudge_batches"], new["rejudge_ids"], new["quality_ids"] = [], [], [], []
    new["usage"] = {}
    new["notes"] = keep_notes
    return new


def note(st: dict, msg: str) -> None:
    st.setdefault("notes", []).append(f"{now_iso()} {msg}")
    st["notes"] = st["notes"][-30:]


def add_usage(st: dict, model: str, usage: dict) -> None:
    u = st.setdefault("usage", {}).setdefault(model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
    for k in u:
        u[k] += int(usage.get(k, 0) or 0)
