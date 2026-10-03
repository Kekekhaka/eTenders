# eTenders digest — Azure Function

Twice a day (08:00 and 20:00 SAST) this Azure Function fetches the last few days of government tenders from the eTenders OCDS API, keeps open ones in the wanted categories nationwide, flags new tenders and those closing within 7 days, and emails a ranked HTML summary plus an xlsx. It costs about US$0.07/month.

The eTenders API is unreliable, so it is called one day at a time, newest first, under a wall-clock budget. If some days cannot be fetched, the email still goes out, marked **Partial digest** and naming the missing dates.

## Files

| File | Purpose |
|------|---------|
| `function_app.py` | Timer trigger (06:00/18:00 UTC = 08:00/20:00 SAST); orchestrates the run: read memory, fetch, build, send email, save memory, log summary. |
| `pipeline.py` | Fetch tenders day by day, validate fields, filter by open status and category, score by keyword, order by score. |
| `memory.py` | Keeps state between runs: which tenders were already sent, which dates were covered. In Azure it is a managed-identity blob; locally a JSON file. |
| `report.py` | Renders the digest into HTML email body, subject line and xlsx. Pure; does no I/O. |
| `email_sender.py` | Sends the email via Azure Communication Services (ACS). |
| `digest.py` | Data contract: types shared between pipeline and report. |
| `TG.py` | Local runner: writes xlsx and HTML email preview without sending. Safe for testing (uses local file memory). |
| `tests/` | Offline unit tests; CI runs them before every deploy. |

## Tuning: edit in `pipeline.py`

- **CATEGORIES** (line 97): a set of lowercase eTenders category names to keep. This is the main volume control; remove entries to get fewer tenders. An **empty set means no category filter** (everything open is kept).
- **PROVINCES** (line 92): set of province names (lowercase, e.g. `"gauteng"`, `"western cape"`). Empty set = nationwide.
- **KEYWORDS** (line 55): dict mapping keywords to weights. Higher weight = ranks higher. These *order* the list; they do not filter.
- **LOOKBACK_DAYS** (line 29): how many days back to fetch (window is today minus N to today, inclusive).
- **CLOSING_SOON_DAYS** (line 30): a tender is flagged "CLOSING SOON" if it closes within this many days (default: 7).

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q          # offline tests
python TG.py                 # builds etenders_matches.xlsx and etenders_digest.html
```

**Python version:** Azure runs Python 3.11. Use 3.11 syntax only. The local venv may be newer (3.12 here); every file parses under the 3.11 grammar, but the tests have not been run on a real 3.11 interpreter.

## Deploy to Azure

1. **Settings** (set these on the Function App via `az functionapp config appsettings set`; never commit them):
   - `ACS_CONNECTION_STRING` — the Azure Communication Services connection string.
   - `SENDER_EMAIL` — the verified sender address (e.g. `DoNotReply@xxxxxxxx.azurecomm.net`).
   - `RECIPIENT_EMAIL` — one or more email addresses (comma-separated).
   - `HEARTBEAT_URL` (optional) — a Healthchecks.io ping URL for silent-failure detection (off by default).

2. **Deploy:** push to `main`. The GitHub Actions workflow runs tests and deploys only if they pass. Watch the Actions run. For rollback, see `docs/cosmos/DEPLOY_PLAN.md`.

3. **Pending operator steps:** the Function App needs a managed identity and a storage container for memory. Until these are set up, every email shows "memory unavailable". See `docs/cosmos/DEPLOY_PLAN.md` **section 2** for the exact steps.

## How to read the email

- **Banners** at the top: **Partial digest** (some days missed), **Memory unavailable** (state could not be read).
- **NEW** label: tender was not in last run's state; you did not receive it before.
- **CLOSING SOON** label: closes within 7 days (configurable).
- **xlsx:** one `Tenders` sheet. Columns are defined by `TENDER_COLUMNS` in `digest.py` (tender number, title, buyer, province, category, closing and published dates, days left, `is_new`, `closing_soon`, score, matched keywords, blockers, document links, and more).

## Known limits

- **Heartbeat monitoring:** optional and off by default (Healthchecks.io free plan; see DEPLOY_PLAN.md section 5).
- **Freshness:** not measured or logged.
- **First run:** every tender is marked NEW. Gmail may clip the email (≈102 KB limit); view the full message via "view entire message" link or open the xlsx.
- **Optional heartbeat:** no cost by default. If enabled, verify the Healthchecks.io free plan includes email alerts before relying on them.

## Decisions and history

- **Architecture decisions** (ADRs): `docs/adr/` — each records context, decision and consequences.
- **Team decisions:** `docs/cosmos/TEAM_LEADER_DECISIONS.md` — binding rules for all agents.
- **Sign-off:** `docs/cosmos/SIGN_OFF.md` — what was built, gate results, open risks.
- **Agent team:** `tools/cosmos_agents.py` installs the ProjectCosmos agents into any project (not deployed).
- **Full specification:** `docs/cosmos/REQUIREMENTS_PACK.md`, `docs/cosmos/DESIGN_SPEC.md`, `docs/cosmos/API_CONTRACT.md`.

Cost verified as US$0.068/month ≈ R1.09/month (2026-09-30, Microsoft's rate R16.00 per US$).
