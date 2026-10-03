# ADR-0024: Contain secrets in their helpers, and redact centrally

- **Status:** Accepted for build (Team Leader review decision B3, 2026-09-30). Not built yet.
- **Date:** 2026-09-30
- **Requirements:** NFR-15, NFR-08(a), FR-10; AC-22, AC-30, AC-33, AC-38
- **Source:** DESIGN_SPEC v0.2 D-9, §7 TB3; Security S-02, S-03; baseline weakness B8

> **Amendment (TL-4, `docs/cosmos/TEAM_LEADER_DECISIONS.md`, 2026-09-30): redaction by containment.**
> - Helpers return a status and never re-raise or chain exceptions that carry URLs or headers.
> - One `redact()` is applied to the **failure-notice text** and the **top-level exception log**.
> - `urllib3` and `azure` loggers stay at WARNING.
> - The AC-30 leak test stays.
> - The **`RedactingFilter` on the root logger and its handlers is dropped.** With the managed identity
>   no secret travels in a URL, and the heartbeat UUID is low-sensitivity (S-03).
> - Security re-checks this at stage 7.
>
> The filter bullet below is superseded. **API_CONTRACT v0.2 §3.6** (authoritative) adds two rules:
> - the top-level handler logs `redact(traceback.format_exc())` with `logging.error`, and never uses
>   `logging.exception` or `exc_info`;
> - the redacted re-raise stays: `raise RuntimeError(text) from None`.

## Context
`requests` exceptions carry the full URL in `str(exc)`. urllib3 logs request paths at DEBUG. Today
the exception text goes to App Insights (`function_app.py:27`), into the failure email
(`email_sender.py:92`) and, through the re-raise (`function_app.py:32`), into the host's
`exceptions` table. The design adds `HEARTBEAT_URL` (a capability URL), a bearer token and the
`IDENTITY_HEADER` value. Each of them must stay out of every one of those sinks.

## Decision
- **Contain:** the blob, token and heartbeat helpers catch `requests.RequestException` inside the
  helper and return a status string (`ok`, `failed:<Class>`, `http:<code>`). They never raise, wrap
  or chain (`raise … from exc` is not allowed). Tokens, `IDENTITY_HEADER` and token-endpoint
  response bodies are never logged.
- **Redact centrally:** one `redact(text)` in `function_app.py` replaces each of these with `REDACTED`:
  - the configured `HEARTBEAT_URL` and its path;
  - the `IDENTITY_HEADER` value;
  - `Bearer …` values;
  - JWT-shaped strings;
  - `"access_token": "…"`;
  - `accesskey=…`;
  - SAS-style `sig|se|sv|sp|si=…` parameters.

  It is used in three places:
  - for the failure-notice text;
  - by a `RedactingFilter` on the root logger **and** its handlers, which redacts the message and
    the formatted traceback and then clears `exc_info`;
  - for the re-raise: `raise RuntimeError(redact(...)) from None`.
- `urllib3` and `azure` loggers are held at WARNING. `host.json` `logLevel` is never Debug or Trace.
- A **leak test** (AC-30) gates the deploy.

## Consequences
- The host's `exceptions` table holds only redacted text, with no chained cause. The full redacted
  traceback is in the trace log.
- The failure notice still names the error class and a redacted message, so it stays useful (FR-10).
- Any new secret-bearing setting must be added to `redact()`. The leak test is the safety net.
