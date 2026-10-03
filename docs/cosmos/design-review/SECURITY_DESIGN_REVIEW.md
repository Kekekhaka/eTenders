# SECURITY_DESIGN_REVIEW: eTenders digest (stage 3)

- **Reviewer:** Security Engineer · **Date:** 2026-09-30
- **Under review:** `docs/cosmos/DESIGN_SPEC.md` v0.1 (baseline `b7d4996` + uncommitted `.funcignore`)
- **Also read:** `REQUIREMENTS_PACK.md` (NFR-08, NFR-11, AC-21), ADR-0008, ADR-0009, `pipeline.py`,
  `email_sender.py`, `function_app.py`, `TG.py`, `requirements*.txt`, `host.json`,
  `.github/workflows/deploy.yml`, `.funcignore`, `.gitignore`, `README.md`
- **Method: static only.** I did not call the eTenders API, Healthchecks or any blob endpoint, and I
  did not read `local.settings.json`. I ran:
  - read-only `az ... show` for non-secret settings (§A);
  - one offline script in the repo venv (pandas 3.0.6, openpyxl 3.1.5, requests 2.34.2). Its only
    network use was a refused connection to `127.0.0.1`. Results are quoted below as "verified offline".

## Summary

| ID | Topic | Class | Before build? |
|---|---|---|---|
| S-01 | H-1: blob auth. SAS vs managed identity | Design concern | **Must decide.** Recommend MI over plain REST (0 new packages) |
| S-02 | Secret URLs leak via exception text into App Insights and the failure email | Probable vulnerability (in design as written) | **Must change** |
| S-03 | Healthchecks.io trust boundary (TB5) | Design concern | Add controls in design; operator accepts residual (H-5) |
| S-04 | Malformed API data crashes the run, so no digest (TB1) | Potential weakness, code path confirmed offline | **Must change** |
| S-05 | Excel formula injection fix (D-6) | Confirmed vulnerability today; the fix is correct | Extend the test and the hyperlink rules in design |
| S-06 | HTML escaping in `report.py` | Defence-in-depth | Put the rules in the §5.3 contract |
| S-07 | ADR-0008: publish profile, SCM **and FTP** basic auth on | Potential weakness | Residual until stage 8 (DevOps); disable FTP now |
| S-08 | CI supply chain: tags, no `permissions`, unlocked transitive deps | Potential weakness | Residual until stage 8 (DevOps) |
| S-09 | `local.settings.json` live keys in a OneDrive-synced folder | Potential weakness | Operator action; not design-blocking |
| S-10 | Memory blob read back as input (TB2 inbound) | Design concern | **Must change** (strict load validation) |
| S-11 | Minor and informational items | Informational | No |

---

## S-01 · H-1: SAS URL vs managed identity. **Recommend: managed identity, over plain `requests`**

**Class:** design concern. **Before build:** must decide, because it changes `BlobStore.__init__`, §6.3 and H-3.

**Evidence.** `identity: null` on `etenders-digest-func` (verified, §A). Account shared-key access is at
its default (allowed). The design weighs "SAS + `requests`" (0 packages, expiring secret) against
"MI + `azure-identity` + `azure-storage-blob`" (3 packages plus native `cryptography`). There is a
third option it does not list, and it is better than both.

**Recommendation.** Enable the system-assigned identity and grant **Storage Blob Data Contributor
scoped to container `digest-state`**. Call the Blob REST API with the `requests` library the app already uses:
1. Token: `GET $IDENTITY_ENDPOINT?resource=https://storage.azure.com/&api-version=2019-08-01` with
   header `X-IDENTITY-HEADER: $IDENTITY_HEADER`. Both env vars are injected by the platform. Read `access_token`.
2. Blob: `GET`/`PUT https://etendersfuncstorage.blob.core.windows.net/digest-state/memory.json` with
   `Authorization: Bearer …` and `x-ms-version: 2021-08-06`. The ETag and If-Match logic is unchanged.

Why this option:
- There is **no secret at all**. `MEMORY_BLOB_URL` becomes a plain, non-secret URL. The token travels in a
  header, and `requests` exception text never contains headers. That removes the blob half of S-02.
- There is no expiry to manage, so the "SAS < 30 days" notice and the `sas_expires` log field can be
  dropped (less code).
- The token can only be minted from inside the app sandbox. Anyone able to mint one already has code execution.
- It keeps working if shared-key access is disabled later. A service SAS is signed with the account key,
  so rotating that key silently kills the SAS.
- It adds about 15 lines. `BlobStore(url, get_token)` takes the token function as an argument, so
  offline tests use a fake. `TG.py` still uses `FileStore`, and FR-22 is unchanged.

**Not verified:** I have not run the identity endpoint on this Linux Consumption app. DevOps should
confirm it on first deploy. The role assignment needs Owner or User Access Administrator rights and can
take a few minutes to propagate, so the first run may show `unavailable`, which is acceptable.

**If the operator still prefers SAS** (accepted residual risk), all of these are conditions:
- (a) **blob-scoped** (`sr=b` on `memory.json`, not the container), permissions `rw` only, `spr=https`,
  bound via `si=` to the container's stored access policy;
- (b) expiry of ≤ 1 year is recommended, and 2 years is acceptable only with the expiry notice;
- (c) S-02 is implemented in full;
- (d) the SAS never goes in `local.settings.json`, the repo or CI.

The blast radius of a leaked SAS is low for confidentiality: public tender ids. It is moderate for
integrity. An attacker can rewrite `sent` or `days` to suppress "new" flags or hide missed days. That
defeats NFR-04 honesty silently.

## S-02 · Secret-bearing URLs in exception text (Q2)

**Class:** probable vulnerability in the design as written. **Before build:** must change.

**Evidence (verified offline).** Both `requests.ConnectionError` and `HTTPError` (from
`raise_for_status`) carry the full URL, including `sig=…`, in `str(exc)`. urllib3 also logs the path with
its query string at DEBUG (`urllib3/connectionpool.py:550-560`). There are three sinks:
- `function_app.py:27` `logging.exception` sends the message to App Insights (90-day retention).
- `email_sender.py:92` puts `{error}` in the failure email, which is a mailbox outside Azure.
- If the exception escapes `run()`, the Functions host records it in `exceptions` regardless of any Python log filter.

The same applies to `HEARTBEAT_URL`, whose UUID is in the path. §7 TB3 says "log only class and status",
but that is a coding convention with no enforcement.

**Recommendation (root cause first):**
1. **Contain it.** `BlobStore` and `_heartbeat` catch `requests.RequestException` inside the helper.
   They return a status string (`"ok" | "failed:<Class>" | "http:<code>"`) and never re-raise, wrap or
   chain the exception. That means no `raise … from exc`, because the chained cause is printed in tracebacks.
2. **Scrub centrally.** Add one `redact(text)` in `function_app.py` that replaces
   `(sig|se|sv|sp|si)=[^&\s'"]+` and the configured `HEARTBEAT_URL` value with `REDACTED`. Apply it to
   the error text in `send_failure_notice`, and also as a `logging.Filter` on the root logger (defence in depth).
3. **Keep noisy loggers quiet.** Pin `urllib3` to WARNING, and never set `host.json` `logLevel` to
   Debug or Trace for `Function`/`Host.Function`.
4. **Tests (NFR-08, AC):** force a `ConnectionError` and a 403 from a fake blob and a fake heartbeat
   whose URLs contain `sig=SECRET` and `/SECRET-UUID`. Assert that `SECRET` is absent from `caplog`, from
   the failure-notice body and from `RUN_SUMMARY`.

With S-01 (MI), items 1–4 still apply to `HEARTBEAT_URL`, but the high-value secret is gone.

## S-03 · Healthchecks.io dead-man's switch (Q3)

**Class:** design concern (a new third-party boundary). **Before build:** fold the controls into §6.2
and §7. The operator accepts the residual risk (H-5).

**What leaves Azure:** one HTTPS request to `hc-ping.com` per run. Healthchecks stores the source IP (an
Azure outbound IP), the user-agent (`python-requests/…`), the timestamp and the body (≤ 100 kB). The
Healthchecks account also holds the operator's alert email address. Nothing else is needed.

**How sensitive the ping URL is:** it is a low-sensitivity **capability URL**. It is write-only: it cannot
read anything, and the management API uses a separate key. Whoever holds it can:
- send success pings, masking a real outage. This loses detection, which is what FR-11 exists to provide;
- send `/fail` pings, which is a nuisance.

It exposes no data. Treat it as a secret setting and redact it (S-02), as the design already does.

**Controls to specify:**
- **Body = fixed allow-list:** `outcome`, `counts`, `run_id`. **Never** `str(exc)`, URLs or tender
  text, because an exception message could carry the SAS (S-02) to a third party.
- `allow_redirects=False`, 5 s timeout, response body ignored (status only). A compromised endpoint
  can't then bounce the Function to an arbitrary URL, and nothing it returns is trusted.
- Validate at startup that `HEARTBEAT_URL` starts with `https://hc-ping.com/`. Otherwise report
  `heartbeat=misconfigured` and send no ping.
- The operator turns on 2FA for the Healthchecks account, because it controls where alerts go. Alerts
  should go to a mailbox that does not depend on ACS.

**If it is compromised:** an attacker can suppress or forge alerts and send the operator email
from Healthchecks. There is no path into Azure, because the traffic is outbound only and nothing in the
response is used. **If it goes away:** pings fail open (logged, never raised) and detection is
lost silently, which is back to today's position. Compensating controls:
- the `heartbeat` field in `RUN_SUMMARY`;
- the design's own optional R0 metric alert on failed requests (§8.2) as a second channel;
- a note in the ops runbook to glance at the check's "last ping" monthly.

Healthchecks.io is EU-hosted. The only personal data it holds is the operator's own address
(informational for POPIA).

## S-04 · Untrusted API shape can kill the digest (TB1)

**Class:** potential weakness, availability (CWE-20 / CWE-754). The code path is confirmed offline.
Exploiting it needs the upstream API to serve malformed data, which it is known to do under load
(ADR-0003). **Before build:** must change. §7 TB1 claims "type-checked JSON", but only the top-level
object is checked (`pipeline.py:123`).

**Evidence (verified offline, today's `select_tenders`):**
- `{"tender": "x"}` raises AttributeError;
- a non-dict release raises AttributeError;
- `title` as an object raises KeyError;
- `documents: ["u"]` raises AttributeError;
- `"bad\x0btitle"` → `to_excel` raises `IllegalCharacterError`.

Each one escapes `select_tenders` or `send_report`, so the run sends a failure notice instead of a digest.
This repeats every run while the bad release stays in the 6-day window. That breaks NFR-03, and one
hostile or garbled release blocks every other tender.

**Recommendation.** Validate at the boundary in `flatten`:
- `releases` must be a list and each release a dict. Nested objects are taken only when they are
  dicts (`_obj(x)`) and text only when it is a `str` (`_text(x)`); anything else becomes null.
- Strip `openpyxl.cell.cell.ILLEGAL_CHARACTERS_RE` from text.
- Drop a release whose `ocid` is not a non-empty `str`. The memory uses it as a dict key.
- Count the dropped releases in `RUN_SUMMARY`.

Keep NFR-05: the code must not use a blanket `except`. Add offline tests with the four payloads above
plus a control character.

## S-05 · Excel formula injection (NFR-08c, AC-21, D-6)

**Class:** confirmed vulnerability today (`email_sender.py:78` and `TG.py:16` use raw `to_excel`). The
design fix is sound. **Before build:** extend AC-21 and the hyperlink rules in §8.5.

**Is escaping `=` enough?** For **xlsx**, yes, provided the fix is the type fix in D-6 and not text
escaping. Verified offline: openpyxl stores as a formula only strings that start with `=`, including
DDE `=cmd|' /C calc'!A0`. Strings that start with `+`, `-`, `@`, tab or CR are stored as typed strings
(`s`), and Excel does not evaluate a typed string cell on open. The `+ - @ \t \r` list is a
**CSV** injection rule. Forcing `data_type "f"→"s"` removes every `<f>`, so it is the root-cause fix.

Recommendations:
- **Don't** prefix values with `'` or a space. That corrupts the data and isn't needed for xlsx.
- Optional defence in depth: set `cell.quotePrefix = True` (supported, verified) on text cells that
  start with `= + - @ \t \r`. Otherwise a user editing the cell (F2, Enter) makes Excel re-parse it into a live formula.
- **Never** add a CSV export of this data without that CSV prefix rule.
- Parametrise the AC-21 test over `=1+1`, `+1+1`, `-1+1`, `@SUM(A1)`, `\t=1+1`, `\r=1+1`, the DDE
  payload and `=HYPERLINK(...)`. Assert `data_type == "s"` after `load_workbook`, and that there is no
  `<f>` in any sheet XML, **the Coverage sheet included**.
- **Hyperlinks** (§5.3 `document_urls`):
  - reject anything containing whitespace or control characters;
  - `urlsplit`; require `scheme.lower() in {"http","https"}` and a non-empty `netloc`;
  - cap the length at 2,000 characters.

  This blocks `file://` and UNC links, which would leak NTLM hashes, and `javascript:` links. Apply
  the same check in `flatten` (backend) and again at the sink in `report.py`.

## S-06 · HTML escaping in `report.py`

**Class:** defence-in-depth. **Before build:** state these rules in the §5.3 contract for Frontend.
- Use one helper, `_e(x) = html.escape("" if null else str(x), quote=True)`, applied at **every**
  interpolation, including banners, `PartialDay.reason`, missed-day dates, the counts and the xlsx-only note.
  Escape by default; nothing is marked "already safe".
- Double-quote all attributes. `href` values must pass the S-05 URL check first, then be escaped. **No API value
  may enter `style`, `class` or any tag or attribute name.** Colours come from constants keyed on backend booleans.
- Show the link's host as its text, or next to it, so the operator can see where a link goes (anti-phishing).
  Render `contact` as plain text, not `mailto:`.
- The subject is built from counts and constants only, with no API text (the design already says so; keep it).
- Test: a `Digest` whose every text field is `<script>"'&` plus a `javascript:` URL. Assert that no raw
  `<script` or `javascript:` appears, and that quotes inside `href` are escaped.

## S-07 · ADR-0008: deploy credential

**Class:** potential weakness. **Before build:** no, this is not a code-design issue. It is residual
risk until stage 8, and DevOps should fix it before this release goes live.

**Evidence (verified, §A):**
- `basicPublishingCredentialsPolicies/scm` `allow=true` and **`ftp` `allow=true`**;
- the SCM site allows IP "Any";
- the profile is a repo secret (`deploy.yml:47`);
- `README.md:123` tells the user to print the profile to the terminal.

The profile is a long-lived password usable from anywhere. Anyone holding it can deploy code, and that
code runs with every app setting: the ACS key, the heartbeat URL and a SAS if S-01 isn't adopted. So a
leak equals full compromise of the app. As a repo secret, any workflow on any branch can read it.

**Recommendation:**
- **Now, at no cost:** disable FTP basic auth. Nothing uses it.
- **Stage 8:** move to `azure/login` with OIDC and a federated credential:
  - subject `repo:<owner>/<repo>:environment:production`;
  - a GitHub `production` environment restricted to `main`;
  - role Website Contributor scoped to the function app only;
  - `permissions: id-token: write` on the deploy job only.

  Then disable SCM basic auth, delete the repo secret and reset the publish profile.

DevOps must verify that `Azure/functions-action` supports RBAC deploys on Linux Consumption. I did not check it.

## S-08 · CI supply chain (NFR-11)

**Class:** potential weakness. **Before build:** no. It is residual risk until stage 8 (DevOps).

**Evidence:**
- `deploy.yml:16`, `18`, `32`, `34` and `42` use mutable tags (`@v7`, `@v1`). The tag-repointing
  pattern was used in the `tj-actions/changed-files` compromise (2025), and `Azure/functions-action`
  directly handles the deploy secret.
- There is no `permissions:` block, so `GITHUB_TOKEN` gets the repo default.
- Transitive dependencies are unlocked (ADR-0009). **`test` and `deploy` resolve them separately**
  (`deploy.yml:23` vs `39`), so the tested set can differ from the deployed set.

**Recommendation:**
- Pin actions by full SHA with a `# vX.Y.Z` comment, and let Dependabot (`github-actions`) bump them.
- Add a top-level `permissions: contents: read`.
- Generate a hashed lock (`pip-compile --generate-hashes`, or `pip freeze` into a constraints file) and
  install with `--require-hashes` in both jobs.
- Optionally run `pip-audit` in the test job.

S-01's REST approach keeps the dependency count at 5. The SDK option would add `cryptography` and
`azure-identity`'s transitive dependencies.

## S-09 · Secrets hygiene: `local.settings.json` in OneDrive

**Class:** potential weakness. **Before build:** no; this is operator action. I did not open the file.

**Evidence:**
- It exists (891 bytes). It is ignored by `.gitignore:1` and `.funcignore:8`, and it has never been
  committed (`git log --all -- local.settings.json` is empty).
- The brief states it holds live keys.
- OneDrive puts it in the NTT tenant, where admins and eDiscovery can see it, it is kept in version
  history and the recycle bin, and it syncs to every signed-in device and to any sharing link on the folder.

**Recommendation:**
- `TG.py` needs no secrets, so keep only non-secret values in this file. Put secrets for `func start`
  in user environment variables, or move the working copy outside OneDrive.
- **Rotate the ACS access key afterwards**, because old versions persist in OneDrive history.
- The new design must never place the SAS or `HEARTBEAT_URL` in this file.
- Note: CI builds from a clean checkout, so `.funcignore`'s secret entries only protect local `func ... publish`.
- H-3 must add `etenders_memory.json` and `etenders_digest.html` to both ignore files.

## S-10 · Memory blob as untrusted input (TB2 inbound)

**Class:** design concern. **Before build:** must change. It's cheap: specify it in §5.2.

**Evidence.** §7 says the code parses with `json.loads`, checks `schema`, and treats anything unexpected
as `unavailable`. A document that passes those checks but has the wrong types still breaks. For
example, `sent` as a list, or `first_sent` as a number. It fails inside the pure functions, which by
NFR-05 raise, so the run crashes and no digest goes out. A tampered or hand-edited blob can therefore block delivery.

**Recommendation.** `load()` validates the full shape and converts it to typed values: dates become
`dt.date` or `datetime`, and `ocid` keys must be `str`. Any failure means `unavailable`, which also
means no write. Cap the read at 5 MB (`stream=True`). Clamp the `closing` value used for retention to
`run_at + 1 year`, so an absurd date (year 9999) can't pin an entry forever or overflow datetime
arithmetic. Only typed values reach the banners.

## S-11 · Informational

- **TLS:** never add `verify=False`, even behind the corporate proxy. Use `REQUESTS_CA_BUNDLE`. HTTPS-only and
  TLS 1.2 are confirmed on the app, the SCM site and the storage account.
- **Storage** `networkRuleSet.defaultAction=Allow` and shared-key access at its default are needed
  while `AzureWebJobsStorage` uses a key. Identity-based host storage is a later hardening step, not required now.
- **App** `publicNetworkAccess=Enabled` with an IP rule of "Any" is fine for a timer-only app with no HTTP triggers.
- **PII:** `contact` (buyer email addresses, public data) appears in the email and xlsx as it does today. The memory holds none (NFR-08e holds).

## A. Configuration verified (read-only, 2026-09-30)

| Item | Value |
|---|---|
| App `httpsOnly` / `minTlsVersion` / `scmMinTlsVersion` / `ftpsState` | true / 1.2 / 1.2 / FtpsOnly |
| App identity | none |
| Basic auth `scm` / `ftp` | **allow=true / allow=true** |
| App / SCM IP restrictions | Allow Any / Allow Any |
| Storage HTTPS-only / min TLS / public blob / network default | true / TLS1_2 / false / Allow |
| Storage `allowSharedKeyAccess` / `sasPolicy` / `keyPolicy` | default (allowed) / none / none |

## B. Changes the Architect should fold in (checklist)

1. S-01: pick MI-over-REST, and update D-1, §6.1 `BlobStore`, §6.3, §7 TB2, T-1, H-3 and ADR-0016.
   Or record the SAS conditions (a)–(d) as an operator-accepted risk.
2. S-02: containment, `redact()`, the logger levels and the leak test go into §7 TB3 and §10 NFR-08.
3. S-03: the body allow-list, `allow_redirects=False`, the URL prefix check and 2FA go into §6.2 and
   TB5. Add the residual note to ADR-0018.
4. S-04 and S-10: boundary validation in `flatten` and strict validation in `load()`, each with tests.
5. S-05 and S-06: the parametrised AC-21 test, the hyperlink rule, and the escaping rules in the §5.3 contract.
6. S-07, S-08 and S-09: hand off to DevOps and the operator (stage 8). Record them as accepted residual risk until then.
