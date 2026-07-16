# CDR/IPDR Investigation Dashboard — Differentiator Feature Specification

**Purpose of this document:** This is a build spec for an AI coding agent to implement a set of advanced features on top of the existing CDR/IPDR Investigation Dashboard (FastAPI + Pandas + Cytoscape.js + ReportLab stack). Each feature includes rationale, required schema/data changes, backend logic, API endpoints, and frontend UI requirements. Implement features independently — each section is self-contained enough to be built and tested on its own.

---

## 0. Context: Existing System (for the agent's reference)

- **Backend**: FastAPI (Python 3), Pandas for CSV ingestion/normalization, ReportLab for PDF export
- **Frontend**: Vanilla HTML/CSS/JS (`app.js`), Cytoscape.js for network graphs
- **Storage**: In-memory Python lists (`cdrs_db`, `ipdrs_db`) — MVP stage, no persistent DB yet
- **Existing schemas**:
  - `CDRRecord`: subject_id, event_type, caller, callee, start_time, duration, cell_id, imei, imsi, operator
  - `IPDRRecord`: subject_id, session_start, session_end, source_ip, source_port, dest_ip, dest_port, protocol, apn
- **Existing endpoints**: `/upload`, `/cdrs`, `/report/pdf` (assume similarly named IPDR equivalents exist)

Before building new features, the agent should first migrate storage from in-memory lists to a persistent database (SQLite is sufficient for a student project; PostgreSQL if deployment is planned) since several features below (chain of custody, case management, cross-referencing) require durable, queryable storage. Use SQLAlchemy as the ORM to keep it portable between SQLite and PostgreSQL.

---

## 1. Chain of Custody & Evidence Integrity Module

**Why**: Makes the tool's output legally defensible, not just analytically useful — the single biggest differentiator from student-grade CDR tools.

**Data model** — new table `evidence_log`:
| field | type | notes |
|---|---|---|
| id | int, PK | |
| case_id | str, FK to `cases` (see Feature 9) | |
| file_name | str | original uploaded filename |
| sha256_hash | str | computed on raw upload bytes, before any parsing |
| uploaded_by | str | investigator name/ID (simple text field, no auth system needed for MVP) |
| upload_timestamp | datetime | server time, UTC |
| record_count | int | rows successfully ingested |
| action | str | "uploaded" / "viewed" / "exported" / "report_generated" |

**Backend logic**:
1. On `/upload`, compute `hashlib.sha256(file_bytes).hexdigest()` **before** passing to Pandas.
2. Insert an `evidence_log` row with action="uploaded".
3. Every time `/report/pdf` or `/cdrs`/`/ipdrs` is called for a given case, insert a new `evidence_log` row with the corresponding action, so there's a full access trail.
4. Add endpoint `GET /case/{case_id}/custody-log` returning the full ordered log for a case.

**Frontend**:
- New "Evidence & Custody" tab per case showing a timeline/table of the log (file, hash, who, when, what action).
- Show the SHA-256 hash prominently next to each uploaded file in the sidebar.

**PDF report change**: Add a "Chain of Custody" appendix section to the ReportLab-generated PDF, listing hash + custody log entries for that case.

---

## 2. Timezone / Clock-Skew Normalization Layer

**Why**: CDR and IPDR sources frequently log in different timezones or have clock drift; comparing raw timestamps produces an incorrect event sequence.

**Data model change**: Add optional fields to both `CDRRecord` and `IPDRRecord`:
- `source_timezone` (str, e.g. "UTC", "Asia/Kolkata") — user-specified at upload time if not present in the CSV
- `normalized_time` (datetime) — computed field, always stored in UTC internally, displayed in a user-selectable display timezone

**Backend logic**:
1. At upload, if the CSV doesn't include timezone info, prompt (via API param) the investigator to specify the source timezone for that file.
2. Use Python's `zoneinfo` to convert all `start_time`/`session_start`/`session_end` to UTC and store both raw and normalized values — never overwrite the original.
3. Add a `GET /case/{case_id}/timeline?tz=<display_tz>` endpoint that returns all CDR+IPDR events sorted by `normalized_time`, converted to the requested display timezone.
4. Flag any file where declared timezone + observed timestamp pattern seems inconsistent (e.g., all calls between 2-5am local — could indicate a mislabeled timezone). This is a soft warning, not a blocker.

**Frontend**:
- On upload, if timezone is ambiguous, show a dropdown to select source timezone before confirming ingestion.
- Global timezone selector at the top of the dashboard that re-renders the timeline/graph in the chosen display timezone.
- Visual badge on any record where a timezone mismatch warning was raised.

---

## 3. Anti-Forensic / Anomaly Flagging

**Why**: Absence of expected data (gaps, IMEI changes) is itself an investigative signal — most tools only show what's present, not what's suspiciously missing.

**Detection logic (implement as a Pandas post-processing step after ingestion)**:
1. **Log gap detection**: For each `subject_id`, sort events by time; if the gap between consecutive events exceeds a configurable threshold (default: 6 hours) during a period where other subjects in the same case show continuous activity, flag it as a potential gap.
2. **IMEI/IMSI churn detection**: For each `subject_id`, if `imei` changes while `imsi` stays constant (or vice versa) within the case's time window, flag as possible SIM/device swap.
3. **Burst-then-silence pattern**: Flag subjects with a sudden spike in call/session frequency immediately followed by complete silence (a common pattern before someone "goes dark").

**Data model**: New table `anomaly_flags`: id, case_id, subject_id, flag_type (enum: gap/imei_swap/burst_silence), start_time, end_time, description, severity (low/medium/high).

**API**: `GET /case/{case_id}/anomalies` returns all flags for a case, sortable by severity.

**Frontend**: A dedicated "Anomalies" panel/tab with a color-coded list (red/amber/yellow by severity), each clickable to jump to that point in the timeline/graph.

---

## 4. CDR ↔ IPDR Cross-Correlation

**Why**: This is the single most novel feature — nearly no existing tool correlates voice/SMS activity with simultaneous data-session activity for the same subject.

**Backend logic**:
1. For a given `subject_id` within a case, fetch all CDR events and all IPDR sessions.
2. Build an "activity overlap" table: for each CDR event, check if an IPDR session was active (`session_start <= call_time <= session_end`) at the same time, using the **normalized (UTC)** timestamps from Feature 2.
3. Compute an "overlap %" metric per subject: what fraction of voice calls happened while a data session was also active — useful for showing simultaneous device usage or possible dual-SIM/device activity.

**API**: `GET /case/{case_id}/subject/{subject_id}/correlation` returns the overlap table + summary metric.

**Frontend**: A combined timeline visualization — voice calls as one track (horizontal bars/points) and data sessions as a second track directly below, sharing the same time axis, so overlaps are visually obvious. (Can be built with a simple custom SVG/Canvas timeline, or D3.js if the agent wants to add a dependency.)

---

## 5. Statistical Behavior Profiling

**Why**: Moves beyond a simple "most active number" table into genuine statistical analysis (ties directly into standard EDA practice).

**Metrics to compute per subject** (backend, using Pandas):
- Call/session count by hour-of-day (0-23) → histogram data
- % of activity in "odd hours" (configurable window, default 11 PM–5 AM)
- Mean, median, std dev of call duration / session duration
- Burst detection: z-score or IQR-based outlier detection on daily call-count time series to flag unusually high-activity days
- Day-of-week distribution

**API**: `GET /case/{case_id}/subject/{subject_id}/profile` returns all of the above as JSON.

**Frontend**: A "Behavior Profile" panel per subject with:
- Hour-of-day histogram (bar chart)
- Duration distribution (histogram or box plot)
- A plain-language summary line, e.g., "42% of this subject's calls occur between 11 PM–5 AM, notably above the case average of 12%."

Use a lightweight charting approach consistent with the existing vanilla-JS frontend (e.g., Chart.js via CDN) rather than introducing a heavy framework.

---

## 6. Common Contact / Triangulation Analysis

**Why**: Automates a task investigators currently do manually — finding shared contacts between multiple suspects.

**Backend logic**:
1. Accept a list of 2+ `subject_id`s as input.
2. For each subject, build the set of unique callees/callers (CDR) or dest_ips (IPDR).
3. Compute set intersection(s) across all selected subjects.
4. Return the intersecting contacts along with the call/session count and date range of contact with each subject.

**API**: `POST /case/{case_id}/intersect` with body `{"subject_ids": [...]}` → returns common contacts with per-subject interaction stats.

**Frontend**: A "Compare Subjects" tool — multi-select subjects from a dropdown, then render an "intersection" table and, ideally, a Venn-style or highlighted subgraph in the existing Cytoscape graph (highlight shared nodes in a distinct color).

---

## 7. Cell-Tower / Location Clustering (if `cell_id` populated)

**Why**: Approximates movement pattern analysis without needing full GPS data — a common real-world investigative technique.

**Backend logic**:
1. If a case's CDR data includes populated `cell_id` values, build a per-subject sequence of `(timestamp, cell_id)`.
2. Cluster consecutive same-cell_id entries into "dwell periods" (time spent apparently near one tower).
3. If a tower ID → lat/long mapping table is available (may need to be manually uploaded as a reference CSV, since raw CDRs usually don't include tower coordinates), plot movement on a map.

**Data model**: Optional reference table `cell_tower_locations`: cell_id, latitude, longitude, operator.

**API**: `GET /case/{case_id}/subject/{subject_id}/movement` returns the dwell-period sequence (and lat/long if mapping table is present).

**Frontend**: If coordinates are available, render on a simple map (e.g., Leaflet.js via CDN) with a path connecting dwell points in chronological order. If no coordinates, show a simple "tower sequence" list/timeline instead — don't block the feature on having GPS data.

---

## 8. Natural-Language / Structured Query Interface

**Why**: Big usability differentiator — investigators can ask questions instead of manually filtering tables.

**Scope for MVP**: Do NOT attempt a full LLM-powered NL parser unless the agent has access to an LLM API. Instead, build a **structured query builder** that mimics natural language through guided input:
- Subject selector
- Time range picker
- Optional "time of day" filter (e.g., 10 PM–6 AM)
- Optional direction filter (incoming/outgoing)
- Optional counterpart number/IP filter

**API**: `POST /case/{case_id}/query` with a structured filter body → returns matching CDR/IPDR rows.

**Frontend**: A single "Ask a question" panel that assembles the structured filters into a readable sentence as the user builds it (e.g., "Show calls involving [subject] after [10 PM] between [date] and [date]") before executing — gives the natural-language feel without requiring true NLP.

*(Optional stretch goal, only if the agent has an LLM API key available: accept a free-text query, send it to an LLM with a system prompt that maps the query to the structured filter JSON above, then execute the same structured query path.)*

---

## 9. Case Management Layer

**Why**: The current tool treats data as one flat dataset; real investigative tools are organized around cases. This is also a prerequisite for Features 1, 3, 4, and 6, which are all scoped per-case.

**Data model**: New table `cases`: id, case_name, case_number (free text, e.g., FIR number), created_by, created_at, status (open/closed), notes (free text).

All existing tables (`cdrs`, `ipdrs`, `evidence_log`, `anomaly_flags`) get a `case_id` foreign key.

**API**:
- `POST /cases` — create a case
- `GET /cases` — list all cases
- `GET /cases/{case_id}` — case detail + summary stats (record counts, subject counts, date range)
- All existing upload/analysis endpoints get a `case_id` path or query parameter added.

**Frontend**:
- Landing page becomes a "Case List" (card grid or table) instead of jumping straight into the dashboard.
- Selecting/creating a case opens the existing dashboard UI, now scoped to that case.
- Sidebar shows current case name/number persistently.

**Migration note for the agent**: This is the foundational structural change — implement Feature 9 first, then layer Features 1–8 on top of it, since they all assume a `case_id` exists.

---

## 10. Confidence / Evidence-Strength Indicator

**Why**: Adds a layer of analytical honesty that mirrors how real forensic findings are presented — distinguishing direct evidence from inference.

**Approach**: Define a simple tagging convention applied wherever a "finding" is surfaced in the UI or PDF report:
- **High confidence** — directly observed in raw records (e.g., "Call from A to B at time T" — a literal CDR row)
- **Medium confidence** — derived from normalized/corrected data (e.g., timezone-corrected timestamp comparison)
- **Low confidence** — inferred/statistical (e.g., common-tower proximity, behavioral anomaly flags, correlation overlap %)

**Implementation**: Add a `confidence` field (enum) to the anomaly_flags, correlation, and movement-analysis outputs from Features 3, 4, and 7. Every card/panel in the UI that displays one of these derived findings shows a small colored badge (e.g., green/amber/grey) with the confidence level and a one-line tooltip explaining why.

**PDF report**: Every non-raw finding included in the exported report must carry its confidence tag inline, so the document is self-explanatory to a reader with no dashboard access.

---

## Suggested Build Order

For a final-year project with a demo deadline, the agent should implement in this order to maximize visible progress early and de-risk the harder items:

1. **Feature 9** (Case Management) — structural prerequisite
2. **Feature 1** (Chain of Custody) — quick to build, high "wow factor" for evaluators
3. **Feature 2** (Timezone normalization) — needed correctness fix, moderate effort
4. **Feature 5** (Statistical profiling) — leverages existing Pandas pipeline, visually impressive
5. **Feature 3** (Anomaly flagging) — builds on Feature 2's normalized timestamps
6. **Feature 6** (Common contact analysis) — straightforward set-logic, good demo value
7. **Feature 4** (CDR↔IPDR correlation) — the most novel differentiator, do once timestamps are reliable (depends on Feature 2)
8. **Feature 10** (Confidence tagging) — thin layer on top of Features 3/4/7, do last
9. **Feature 8** (Query interface) — polish item
10. **Feature 7** (Cell-tower clustering) — only if cell_id/tower-mapping data is realistically available; otherwise deprioritize

---

## Non-Functional Requirements

- Maintain the existing "premium glassmorphism" visual style already in `styles.css` — new panels/tabs should match, not clash with, current design language.
- Keep the stack additions minimal: SQLAlchemy + SQLite for persistence, Chart.js and/or Leaflet.js via CDN for new visualizations — avoid introducing a frontend build system (Vite/webpack/React) unless explicitly requested, since the project intentionally uses vanilla JS.
- Every new feature must degrade gracefully when its required data field is missing (e.g., no cell_id → skip Feature 7 silently rather than erroring) rather than assuming all optional CSV fields are always present.
