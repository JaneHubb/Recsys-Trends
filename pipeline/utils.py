"""HTTP (retries, rate limiting), JSON/JSONL I/O, logging, run report."""
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
    """requests.Session + exponential backoff. Honors Retry-After on 429."""

    def __init__(self, min_interval: float = 1.0, headers: dict | None = None):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": config.USER_AGENT, "Accept": "application/json"})
        if headers:
            self.s.headers.update(headers)
        self.min_interval = min_interval
        self._last = 0.0
        self.exhausted = 0          # requests that used up all retries; after 2 the rest fail fast (do not burn hours on a dead source)

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def request(self, method: str, url: str, retries: int = 6, timeout: int = 90, **kw) -> requests.Response:
        if self.exhausted >= 2:
            raise RuntimeError(f"{url.split('/')[2]}: skipping, earlier requests kept failing (source looks down)")
        delay = 2.0
        last_exc: Exception | None = None
        for attempt in range(retries):
            self._throttle()
            try:
                r = self.s.request(method, url, timeout=timeout, **kw)
            except requests.RequestException as e:   # connection errors: at most 4 retries (2+4+8+16 s)
                last_exc = e
                if attempt >= 3:
                    break
                log.warning("%s %s connection error (%s), retrying in %.0fs", method, url[:80], e.__class__.__name__, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                ra = r.headers.get("Retry-After")
                wait = float(ra) if ra and ra.isdigit() else delay
                wait = min(max(wait, 1.0), 120) + random.uniform(0, 1)
                log.warning("%s %s → %s, retrying in %.0fs (%d/%d)", method, url[:80], r.status_code, wait, attempt + 1, retries)
                time.sleep(wait)
                delay = min(delay * 2, 60)
                continue
            self.exhausted = 0
            return r
        self.exhausted += 1
        if last_exc:
            raise last_exc
        raise RuntimeError(f"{method} {url}: still failing after {retries} retries")

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)


# ---------- files ----------

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
                        log.warning("%s: skipping a malformed line", path.name)
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


# ---------- run report ----------

class Report:
    """Run summary. Saved as state/last_report.md and used in the Actions summary and Issue bodies."""

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
        out = [f"## {self.title}", f"\nGenerated: {now_iso()}\n"]
        out.extend(self.lines)
        if self.warnings:
            out.append("\n### Warnings\n")
            out.extend(f"- ⚠️ {w}" for w in self.warnings)
        if self.unknown_tracks:
            out.append("\n### Unrecognized track (section) names — add a rule to `tracks` in venues.yaml\n")
            out.append("| Venue-year · section | Papers | Interim handling |\n|---|---|---|")
            out.extend(f"| {k} | {n} | main/short by page-count rule |" for k, n in sorted(self.unknown_tracks.items()))
        return "\n".join(out) + "\n"

    def save(self, state_dir: Path | None = None) -> Path:
        d = state_dir or config.STATE_DIR
        d.mkdir(parents=True, exist_ok=True)
        p = d / "last_report.md"
        p.write_text(self.render(), encoding="utf-8")
        if self.unknown_tracks:
            write_json(d / "unknown_tracks.json", self.unknown_tracks)
            md = ["A DBLP table of contents contained section names that match no `tracks` rule in `venues.yaml`. Those papers were counted as main/short by the page-count rule.",
                  "Adding a rule takes effect on the next run (paste this into a Claude conversation to get the rule written).", "",
                  "| Venue-year · section | Papers |", "|---|---|"]
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
