# SECURITY_REPORT (stage 7, QUICK mode, static review)

Date 2026-10-01. Scope: the files in the brief. Read-only `az` calls only. No live calls to eTenders, ACS, Blob or the heartbeat. `local.settings.json` was never opened.

## Findings

| ID | Class | Evidence | Abuse path / impact | Fix | CVSS |
|---|---|---|---|---|---|
| S-1 | Potential weakness (supply chain) | `.github/workflows/deploy.yml:1-48`: no `permissions:` block; `actions/*@v7`, `Azure/functions-action@v1` pinned by tag only | A compromised tag runs with the default `GITHUB_TOKEN` scope and sees `AZURE_FUNCTIONAPP_PUBLISH_PROFILE` in the deploy job. | Add `permissions: contents: read` at top level (free). Pin the three actions to commit SHAs (free, optional). | 4.0 (AV:N/AC:H/PR:N/UI:N/S:U/C:L/I:L/A:L), est. |
| S-2 | Design concern (deploy credential) | `az resource show` basicPublishingCredentialsPolicies: `scm.allow = true`, `ftp.allow = true`. `ftpsState = FtpsOnly`, `httpsOnly = true`, `minTlsVersion = 1.2` | The publish profile is a long-lived basic-auth credential. If leaked, it allows code deployment, and the code runs with the managed identity. | Free: keep it, rotate the publish profile and the GitHub secret on a schedule. Preferred but more work: switch the deploy to OIDC (`azure/login`) and disable SCM and FTP basic auth. Both are free. Disable FTP basic auth now if nobody uses FTP (free; not done by me). | 5.5, est. |
| S-3 | Defence-in-depth | `function_app.py:89` | `allow_redirects=False` is set and the prefix check is at `:79`, so a hostile redirect cannot move the destination. The prefix check passes `https://hc-ping.com.evil.tld/`? No: the prefix includes the trailing slash, so it does not pass. Closed. | None. | n/a |
| S-4 | Informational | `redact()` `function_app.py:56-68`: the patterns need the `Bearer ` prefix, `eyJ` JWT shape, `access_token`, `accesskey=` or SAS `sig=`/`se=`/`sv=`/`sp=`/`si=`. A raw ACS key or token that appears bare, without these markers, is not redacted. | No current path puts a bare secret in an exception. The ACS connection string reaches only `from_connection_string` (`email_sender.py:39`), and an SDK exception that echoes it would match `accesskey=`. | Optional: also replace the value of `ACS_CONNECTION_STRING` when it is at least 8 characters, as is done for the heartbeat URL. | n/a |
| S-5 | Informational | `memory.py:123-126` | The blob read is streamed and cut off at 5 MB plus one chunk. `_decode` then rejects anything over `MAX_BYTES`. It is bounded. | None. | n/a |

No confirmed or probable vulnerability was found.

## Checklist

1. **No secret reaches a sink: closed.** I ran `C:\...\scratchpad\leak.py`, outside the repo. It put a fake bearer token, JWT, `accesskey=`, SAS `sig=`, heartbeat URL and UUID, and `IDENTITY_HEADER` into an exception raised from `memory.load`. A failing `send_failure_notice` raised the same kinds of values. It then checked the raised `RuntimeError` text, the failure-notice text and all log output captured at DEBUG. Result: **all six values clean**. `__cause__` and `__context__` are both None (`function_app.py:183`), and the heartbeat URL is used only as the ping target. Not run: a real SDK exception, and the Azure host's own logging of the unhandled `RuntimeError`, which carries the redacted text.
2. **Untrusted API data: closed.** The email escapes every value with `html.escape(quote=True)` (`report.py:67`, `_link` at `report.py:108-112`). The failure notice is escaped (`email_sender.py:80`). Links go through `is_safe_url` (`digest.py:95-107`: http(s) only, a host, at most 2000 characters, no whitespace or control characters). It is applied in `pipeline.py:327`, `report.py:108` and `report.py:415`. The xlsx sets `data_type="s"` and `quotePrefix` on any formula-looking cell, on every sheet (`report.py:331-377`, called at `:421`). The prefixes are `= + - @ \t \r`. `strip_unwritable` is at `digest.py:115`. I did not trace the "malformed shapes" path in `pipeline.flatten`. The `_deliver_digest` handler (`function_app.py:165`) turns any crash into a redacted notice, so the run fails safe.
3. **Memory blob: closed.** The size cap is `memory.py:37,125,193`. `json.loads` errors, `RecursionError`, a schema mismatch, a type error or a date error all lead to `unavailable` (`:264-276`). Under `unavailable`, `mem` is None, nothing is marked NEW and nothing is written (`function_app.py:125`). Absurd dates are clamped (`:287-298`). Extra keys are ignored by design (TL-2). A tamperer who can write the blob can only add or remove sent ocids, which suppresses or repeats a tender. That needs write access to the private blob and is accepted as residual risk. The token is never logged (`:63-85`) and the URL is never logged (`:259-283`).
4. **Outbound calls: closed.** The destinations are the fixed `MEMORY_URL` (`memory.py:36`), `IDENTITY_ENDPOINT` (platform-injected, not attacker-controlled), the ACS endpoint (from the operator's settings) and the heartbeat (prefix check at `function_app.py:79`, `allow_redirects=False` at `:89`). Nothing from the API data picks a host.
5. **Supply chain and CI: open (S-1).** Dependencies are pinned with `==` (`requirements.txt`). No hash pinning. There is no lockfile for transitive packages, which is informational. Actions are pinned by tag only. `.funcignore` excludes `tools`, `docs`, `.claude`, `tests`, `local.settings.json`, `TG.py` and `.git*`.
6. **Deploy credentials: accepted for now (S-2), with the free mitigation above.** `httpsOnly=true`, `minTlsVersion=1.2`, `ftpsState=FtpsOnly`. SCM basic auth is allowed, which the publish-profile deploy needs.
7. **Secrets hygiene: closed.** `git check-ignore` confirms `.gitignore:1` covers `local.settings.json`, and `.funcignore` excludes it. A `git log -p --all` pattern count (AccountKey, `accesskey=` followed by 20 or more characters, JWT, a hc-ping UUID, a publishData tag, a SAS `sig=`, an ACS endpoint) returned **0 matches**. No `local.settings.json` or publish-profile file name appears in any commit. Values were not printed.
8. **Cost: closed.** Every fix above is free. Nothing paid is recommended. Microsoft Defender for App Service or Storage was not assessed, so I give no price. I leave it as optional residual risk for the operator, with no recommendation.

## Residual risk

Low. S-1 and S-2 are for the operator to accept or fix. Anyone with write access to the blob can influence NEW marks, but only that. The heartbeat UUID is low-sensitivity (TL-4). Confidence: medium-high, because this was a static review and I did not review `pipeline.py` beyond `is_safe_url`.

## For DevOps (stage 8)

- Add `permissions: contents: read` to `deploy.yml`, and optionally pin actions to SHAs.
- Decide on OIDC login against the publish profile. Rotate the profile either way, and disable FTP basic auth if it is unused.
- Check that the managed identity holds only Storage Blob Data Contributor, scoped to the `digest-state` container.
- Confirm the heartbeat URL stays out of the repo and is set as an app setting only.
