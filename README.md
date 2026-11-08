# RecSys Trends

A GitHub Pages site that shows recommender-systems papers at top-tier AI conferences (RecSys · SIGIR · KDD · WWW · WSDM · CIKM · NeurIPS · ICLR) by venue and year. Collection and classification run automatically on GitHub Actions; the maintainer sets things up once.

**This is stage 1 (the skeleton):** collection → LLM classification of every paper → ⓪ Overview · ① Paper counts · ④ Paper list. Topic tags, one-line summaries and trend labels (stage 2) and notable papers (stage 3) come later.

---

## 1. One-time setup

### 1-1. Keys

| Key | Where | Required? |
|---|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys | Required (classification) |
| `S2_API_KEY` | Request at the [Semantic Scholar API](https://www.semanticscholar.org/product/api) page (approval can take days) | Effectively required. Without it the pipeline runs slowly and hits frequent 429 errors |

In the Claude console, set a **monthly workspace spend limit of $10** (Settings → Limits). The pipeline has its own $9.50 per-run cap, but the console limit is the final safety net.

### 1-2. Create the repository

1. Create a new **public** repository on GitHub (e.g. `recsys-trends`). Private works too, but GitHub Pages then needs a paid plan.
2. Upload the contents of this zip.
   - **If you upload through the web UI**: the hidden folder `.github/workflows` is easy to lose. After uploading, make sure `.github/workflows/update.yml` is visible in the repository. GitHub Desktop or `git push` is safer.
3. **Settings → Pages → Build and deployment → Source**: choose **GitHub Actions**.
4. **Settings → Secrets and variables → Actions → New repository secret**: add `ANTHROPIC_API_KEY` and `S2_API_KEY`.
5. (Optional) On the **Variables** tab, add `MAX_RUN_USD` to change the per-run cost cap (default 9.5).

### 1-3. First run

1. **Actions → update → Run workflow** with `dry_run` enabled. This collects everything but makes no LLM calls. Takes 10–20 minutes.
2. When it finishes, the Summary page lists collected papers per venue-year and the **estimated cost**. If a venue shows 0 papers or there are many warnings, paste the summary into a Claude conversation to fix `venues.yaml`.
3. If everything looks right, run again with `dry_run` off. The backfill classifies every paper since 2018 (roughly 50–60k), so expect **1–3 hours of Batch processing** and about **$7–9**. If the cap is exceeded, the newest years are processed first and the rest is deferred (shown in the summary).
4. Open `https://<account>.github.io/<repo>/`.

On failure an Issue is created automatically. Paste its body into a Claude conversation.

---

## 2. From then on, it runs itself

| Workflow | When | What it does |
|---|---|---|
| **update** | May 20 and November 20, 01:17 UTC | Collect venue-years not yet complete → classify everything with Haiku → re-judge low-confidence papers with Sonnet → aggregate → deploy |
| **advance** | Every 4 hours in May, June, November, December; once a day otherwise | If a Batch submitted by update is still processing, pick it up and finish. Exits in seconds when idle |
| **monthly** | 1st of every month | Heartbeat commit (keeps scheduled workflows from being disabled after 60 idle days) · source health check · re-aggregate · deploy |

- The November run gives a **provisional** picture of the year (7 of 8 venues; CIKM is still too recent). The May run makes the previous year **final**.
- To run manually, open the Actions tab and press `Run workflow`. For example, once CIKM is on DBLP: `venues: CIKM`, `years: 2026`.

---

## 3. Cost

Batch API prices (Haiku 5.5 $0.05 in / $0.25 out, Sonnet 5.5 $1 in / $5 out, per million tokens).

| Run | Estimate |
|---|---|
| First backfill (2018 to now) | $7–9 |
| Scheduled update (twice a year) | $1–3 |
| Full re-classification (after a prompt or model change) | $5–7 |

Safeguards: cost estimate before every run ($9.50 cap, newest years first) · each paper is classified once and cached · console spend limit.

---

## 4. Files

```
venues.yaml              Venue definitions: DBLP TOC names, track rules, conference month, OpenReview settings  ← edited most often
prompts/judge_v1.md      Classification prompt (core / related / no)
pipeline/                Python code
  run.py                 CLI entry point (update · advance · monthly · aggregate · collect)
  collect_dblp.py        DBLP search API + TOC HTML (section → track)
  collect_openreview.py  OpenReview v1 and v2
  enrich_s2.py           Semantic Scholar (abstract, citations, arXiv id)
  judge.py               Claude Batch classification
  aggregate.py           Completion state · summary.json · papers/*.json · pre-deploy validation
  state.py               State machine (a long Batch is picked up by the next run)
site/                    Static site (HTML, CSS, JS; no build step)
data/
  raw/{venue}-{year}.json  Collected records (title, authors, links, track; abstracts are not stored)
  llm/judge.jsonl        Haiku judgment cache · rejudge.jsonl  Sonnet re-judgments
  summary.json           Aggregates for the site's first screen
  status.json            Completion state per venue-year
  papers/{venue}-{year}.json  Recommender papers (core, related) + borderline cases + demo
  quality.json           Haiku ↔ Sonnet agreement on a sample (internal; not shown on the site)
  changelog.json · meta.json · heartbeat.json · health.json
state/pipeline.json      State machine · last_report.md  latest run report
scripts/                 Site assembly, Issue creation, sample data
tests/                   pytest (runs without external APIs)
```

---

## 5. Changing things

- **Add a venue**: add an entry to `venues.yaml`. `color` is a free number from 1 to 8 (a 9th venue is shown in grey).
- **Track rules**: when a DBLP section name matches no rule, an `unknown-track` Issue is created. Paste it into Claude to get the rule, add it, and the next run applies it. Until then those papers are counted as main/short by page count.
- **Prompt changes**: create `prompts/judge_v2.md` and bump `PROMPT_VERSION` in `pipeline/config.py`. The next run re-classifies everything (mixing models would create fake trends, so nothing is reclassified partially).
- **Model changes**: edit `JUDGE_MODEL` / `REJUDGE_MODEL` and `PRICES` in `config.py` together, then run `update` with `force`.

---

## 6. Counting rules (summary)

- **One paper** = an entry in the DBLP TOC or on OpenReview in the `main · short · applied · resource` tracks. Demo is a toggle; keynotes, tutorials, workshops and 1–2 page talk abstracts are excluded.
- **Recommender paper** = the LLM labels each paper `core` (directly about recommendation), `related` (overlapping fields such as ad CTR or search ranking) or `no`. Statistics use `core` by default; `related` is a toggle. Every RecSys paper is `core`.
- **Complete** = all expected volumes are present (KDD has two since 2025), the count is at least 70% of the previous year, and conference month + 1 has passed (for OpenReview venues: a listing exists after decisions).
- **Basis year** = the most recent year with at least 6 complete venues. 8/8 is final, otherwise provisional. Year-over-year comparisons use only venues complete in both years.
- **Deploy blocked** when a completed venue-year changes by more than 20% or the overall total drops by more than 50%; an Issue is created instead. After checking the cause, rerun manually with `skip_validation`.

---

## 7. Local preview (optional)

```bash
pip install -r requirements.txt
python -m pytest -q                      # tests
python scripts/make_sample.py            # aggregate synthetic data (no APIs, no LLM)
bash scripts/build_site.sh sample        # assemble _site/
python -m http.server -d _site 8000      # http://localhost:8000
```

To run against real data locally, set `ANTHROPIC_API_KEY` and `S2_API_KEY` and start small: `python -m pipeline.run update --years 2025 --venues WSDM --dry-run`.

---

## 8. When something goes wrong

1. A failure creates an Issue labelled `pipeline-failure`; later failures are added as comments. Paste the Issue body (error + run report) into a Claude conversation.
2. The run report is also on the Summary page of the run in the Actions tab.
3. If a Batch has not finished after 24 hours, set `phase` in `state/pipeline.json` to `idle`, commit, and rerun `update` (submitted Batches expire on their own after 24 hours).

---

## 9. Sources and licences

- Paper listings: [DBLP](https://dblp.org) (CC0) · [OpenReview](https://openreview.net)
- Abstracts, citation counts, arXiv ids: [Semantic Scholar API](https://api.semanticscholar.org) (abstracts are never stored in the repository)
- Classification: Claude (Anthropic). It can be wrong; borderline cases can be inspected on the site with a toggle.
