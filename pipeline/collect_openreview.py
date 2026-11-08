"""OpenReview collection (all ICLR years, NeurIPS 2021+). Handles API v1 and v2.

- v2 (content.venueid=...): returns accepted papers only. Presentation type is in content.venue.value ("ICLR 2025 oral" etc.)
- v1 (invitation=.../Blind_Submission&details=directReplies): includes rejected papers, filtered via decision notes or the venue string
- Abstracts stay in the work directory and are never stored in the repository
"""
from __future__ import annotations

from . import config
from .utils import Http, Report, log


def _v(x):
    """v2 wraps values as {'value': ...}; v1 returns them bare."""
    if isinstance(x, dict) and "value" in x:
        return x["value"]
    return x


def _badge(text: str | None) -> str | None:
    if not text:
        return None
    t = text.lower()
    if "oral" in t or "talk" in t or "top 5%" in t or "top-5%" in t:
        return "oral"
    if "spotlight" in t or "top 25%" in t or "top-25%" in t:
        return "spotlight"
    return None


def _accepted_v1(note: dict, conf_id: str) -> tuple[bool, str | None]:
    """(accepted?, presentation-type string)"""
    c = note.get("content", {})
    venueid = _v(c.get("venueid"))
    venue = _v(c.get("venue")) or ""
    vl0 = venue.lower()
    if venueid and venueid == conf_id and "submitted" not in vl0 and "reject" not in vl0 and "withdrawn" not in vl0:
        return True, venue
    for rep in (note.get("details") or {}).get("directReplies", []) or []:
        inv = rep.get("invitation", "") or ""
        if "Decision" in inv or "Meta_Review" in inv:
            dec = _v(rep.get("content", {}).get("decision") or rep.get("content", {}).get("recommendation")) or ""
            if "accept" in dec.lower():
                return True, dec
            if dec:
                return False, None
    vl = venue.lower()
    if vl and ("accept" in vl or "poster" in vl or "oral" in vl or "spotlight" in vl) and "reject" not in vl and "submitted" not in vl:
        return True, venue
    return False, None


def _fetch_all(http: Http, url: str, params: dict) -> list[dict]:
    notes: list[dict] = []
    offset = 0
    while True:
        r = http.get(url, params={**params, "limit": 1000, "offset": offset})
        if r.status_code == 404:
            break
        r.raise_for_status()
        batch = r.json().get("notes", [])
        notes.extend(batch)
        if len(batch) < 1000:
            break
        offset += 1000
    return notes


def collect(http: Http, vcfg: dict, year: int, orcfgs: list[dict], report: Report) -> dict:
    """{'papers': [...], 'abstracts': {id: text}, 'volumes': [venueid...]}"""
    vid = vcfg["id"]
    papers: dict[str, dict] = {}
    abstracts: dict[str, str] = {}
    volumes: list[str] = []
    for oc in orcfgs:
        track = oc.get("track", "main")
        if oc["api"] == "v2":
            venueid = oc["venueid"].format(year=year)
            notes = _fetch_all(http, f"{config.OPENREVIEW_V2}/notes", {"content.venueid": venueid})
            key = venueid
        else:
            inv = oc["invitation"].format(year=year)
            notes = _fetch_all(http, f"{config.OPENREVIEW_V1}/notes", {"invitation": inv, "details": "directReplies"})
            key = inv
        conf_id = key.split("/-/")[0]
        n_new = 0
        for note in notes:
            c = note.get("content", {})
            if oc["api"] == "v2":
                accepted, venue_str = True, _v(c.get("venue"))
            else:
                accepted, venue_str = _accepted_v1(note, conf_id)
            if not accepted:
                continue
            nid = note.get("id")
            pid = f"or:{nid}"
            if pid in papers:
                continue
            title = (_v(c.get("title")) or "").strip()
            authors = _v(c.get("authors")) or []
            papers[pid] = {
                "id": pid,
                "venue": vid,
                "year": year,
                "title": title,
                "authors": [str(a) for a in authors],
                "doi": None,
                "url": f"https://openreview.net/forum?id={note.get('forum') or nid}",
                "openreview": nid,
                "pages": None,
                "volume": key,
                "section": venue_str,
                "track": track,
                "badge": _badge(venue_str),
            }
            ab = _v(c.get("abstract"))
            if ab:
                abstracts[pid] = str(ab)
            n_new += 1
        log.info("%s %d: %s → %d papers (%d notes returned)", vid, year, key, n_new, len(notes))
        if n_new:
            volumes.append(key)
        elif notes:
            report.warn(f"{vid} {year}: {key} returned {len(notes)} notes but none judged accepted. Check the openreview settings in venues.yaml")
    return {"papers": list(papers.values()), "abstracts": abstracts, "volumes": volumes}


def fetch_abstracts(http: Http, note_ids: list[str]) -> dict[str, str]:
    """For re-judging: fetch abstracts again by note id (v2 first, then v1)."""
    out: dict[str, str] = {}
    for nid in note_ids:
        for base in (config.OPENREVIEW_V2, config.OPENREVIEW_V1):
            try:
                r = http.get(f"{base}/notes", params={"id": nid}, retries=2)
                if r.status_code != 200:
                    continue
                notes = r.json().get("notes", [])
                if notes:
                    ab = _v(notes[0].get("content", {}).get("abstract"))
                    if ab:
                        out[f"or:{nid}"] = str(ab)
                    break
            except Exception as e:  # noqa: BLE001
                log.warning("OpenReview abstract refetch failed %s: %s", nid, e)
    return out
