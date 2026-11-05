"""HTTP(재시도·속도 제한), JSON/JSONL 입출력, 로깅, 실행 보고서."""
from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from . import config

log = logging.getLogger("recsys")


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today() -> datetime:
    return datetime.now(timezone.utc)


class Http:
    """requests.Session + 지수 백오프. 429의 Retry-After를 존중한다."""

    def __init__(self, min_interval: float = 1.0, headers: dict | None = None):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": config.USER_AGENT, "Accept": "application/json"})
        if headers:
            self.s.headers.update(headers)
        self.min_interval = min_interval
        self._last = 0.0
        self.exhausted = 0          # 재시도를 다 쓴 요청 수. 2회 이상이면 이후 요청은 바로 실패시킨다 (소스가 죽었을 때 몇 시간을 허비하지 않도록)

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def request(self, method: str, url: str, retries: int = 6, timeout: int = 90, **kw) -> requests.Response:
        if self.exhausted >= 2:
            raise RuntimeError(f"{url.split('/')[2]}: 앞선 요청이 계속 실패해 건너뜁니다 (소스 장애로 보임)")
        delay = 2.0
        last_exc: Exception | None = None
        for attempt in range(retries):
            self._throttle()
            try:
                r = self.s.request(method, url, timeout=timeout, **kw)
            except requests.RequestException as e:   # 연결 오류는 4회까지만 재시도 (2+4+8+16초)
                last_exc = e
                if attempt >= 3:
                    break
                log.warning("%s %s 연결 오류 (%s), %.0fs 후 재시도", method, url[:80], e.__class__.__name__, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                ra = r.headers.get("Retry-After")
                wait = float(ra) if ra and ra.isdigit() else delay
                wait = min(max(wait, 1.0), 120) + random.uniform(0, 1)
                log.warning("%s %s → %s, %.0fs 후 재시도 (%d/%d)", method, url[:80], r.status_code, wait, attempt + 1, retries)
                time.sleep(wait)
                delay = min(delay * 2, 60)
                continue
            self.exhausted = 0
            return r
        self.exhausted += 1
        if last_exc:
            raise last_exc
        raise RuntimeError(f"{method} {url}: 재시도 {retries}회 후에도 실패")

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)


# ---------- 파일 ----------

def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj: Any, indent: int = 1) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        log.warning("%s: 깨진 줄 무시", path.name)
    except FileNotFoundError:
        pass
    return rows


def append_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    n = 0
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    os.replace(tmp, path)
    return n


# ---------- 실행 보고서 ----------

class Report:
    """실행 요약. state/last_report.md 로 저장되어 Actions 요약과 Issue 본문에 쓰인다."""

    def __init__(self, title: str):
        self.title = title
        self.lines: list[str] = []
        self.warnings: list[str] = []
        self.unknown_tracks: dict[str, int] = {}
        self.deploy = False

    def info(self, msg: str) -> None:
        log.info(msg)
        self.lines.append(f"- {msg}")

    def warn(self, msg: str) -> None:
        log.warning(msg)
        self.warnings.append(msg)

    def section(self, title: str) -> None:
        self.lines.append(f"\n### {title}\n")

    def unknown_track(self, venue: str, year: int, section: str, n: int = 1) -> None:
        key = f"{venue} {year} · {section}"
        self.unknown_tracks[key] = self.unknown_tracks.get(key, 0) + n

    def render(self) -> str:
        out = [f"## {self.title}", f"\n생성: {now_iso()}\n"]
        out.extend(self.lines)
        if self.warnings:
            out.append("\n### 경고\n")
            out.extend(f"- ⚠️ {w}" for w in self.warnings)
        if self.unknown_tracks:
            out.append("\n### 처음 보는 트랙(섹션) 이름 — venues.yaml 의 tracks 에 규칙을 추가해 주세요\n")
            out.append("| 학회·연도 · 섹션 | 편수 | 임시 처리 |\n|---|---|---|")
            out.extend(f"| {k} | {n} | 쪽수 규칙으로 main/short 처리 |" for k, n in sorted(self.unknown_tracks.items()))
        return "\n".join(out) + "\n"

    def save(self, state_dir: Path | None = None) -> Path:
        d = state_dir or config.STATE_DIR
        d.mkdir(parents=True, exist_ok=True)
        p = d / "last_report.md"
        p.write_text(self.render(), encoding="utf-8")
        if self.unknown_tracks:
            write_json(d / "unknown_tracks.json", self.unknown_tracks)
            md = ["DBLP 목차에서 `venues.yaml` 의 `tracks` 규칙에 맞지 않는 섹션 이름이 나왔습니다. 해당 논문은 쪽수 규칙으로 main/short 로 집계했습니다.",
                  "규칙을 추가하면 다음 실행부터 반영됩니다 (이 내용을 Claude 대화에 붙여넣으면 규칙을 만들어 줍니다).", "",
                  "| 학회·연도 · 섹션 | 편수 |", "|---|---|"]
            md += [f"| {k} | {n} |" for k, n in sorted(self.unknown_tracks.items())]
            (d / "unknown_tracks.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        else:
            for name in ("unknown_tracks.json", "unknown_tracks.md"):
                try:
                    (d / name).unlink()
                except FileNotFoundError:
                    pass
        gh_out = os.environ.get("GITHUB_OUTPUT")
        if gh_out:
            with open(gh_out, "a", encoding="utf-8") as f:
                f.write(f"deploy={'true' if self.deploy else 'false'}\n")
                f.write(f"unknown_tracks={'true' if self.unknown_tracks else 'false'}\n")
        return p


def write_error(msg: str, state_dir: Path | None = None) -> None:
    d = state_dir or config.STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "last_error.txt").write_text(f"{now_iso()}\n{msg}\n", encoding="utf-8")


def chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
