# ADR-0027: AI second look at the tenders the keywords miss

- **Status:** Accepted (operator approval, 2026-10-09)
- **Date:** 2026-10-09
- **Code:** `classify.py`; `pipeline.ai_candidates`; `memory.py` (`ai`); `report.py` (label); `function_app._classify`
- **Evidence:** `tools/phase2_compare.py`, 144 hand-labelled tenders from the July 2026 bulk file

## Context
Keyword matching found 1 of the 4 relevant tenders in the labelled sample. It missed both
software-development tenders and the RAF Oracle database support tender. In the same test:

| Model | Found | False alarms | Notes |
|---|---|---|---|
| Llama 3.3 70B (Cloudflare) | 4 of 4 | 2 | best recall |
| Llama 3.1 8B (Cloudflare) | 3 of 4 | 1 | |
| gpt-5-nano (Azure) | 3 of 4 | 5 | least consistent between runs |

The sample has only 4 positives, so this is evidence, not proof.

## Decision
- Every open tender that the keyword and consulting rules leave out goes to **Llama 3.3 70B on
  Cloudflare Workers AI, on the free plan**.
- If Cloudflare fails, the remaining tenders go to **gpt-5-nano on Azure OpenAI**. Cloudflare counts as
  failed after 3 errors, or after one 401, 403 or 429, the reply it gives when the free allowance runs
  out.
  - nano is reached with the Function's **managed identity**: no key is stored.
  - nano is capped at **R0.20 per run**.
- A tender neither model answers keeps the keyword result and is retried next run.
- **Verdicts are remembered** in the run memory (`ai`, kept 14 days), so each tender is judged once.
- Kept tenders carry an email label, "AI-flagged (Llama)" or "AI-flagged (nano)". The email warns that
  nano is less accurate.
- **Time limit:** no AI call starts after 90 s of classifying, or after 480 s into the run. The
  Function timeout is 600 s.
- **Switch:** off unless `LLM_ENABLED=true`.

## Cost (ADR-0026)
- **Cloudflare:** free plan, 10,000 neurons a day; calls fail rather than bill when it's used up.
  - Measured: about 7.1 neurons per tender. The dashboard showed 2.42k neurons after the test runs,
    within about 2% of the estimate.
  - Steady state: about 230 new tenders a day, about 1,600 neurons.
- **Azure gpt-5-nano:** R0.80 in and R6.40 out per million tokens (Azure Retail Prices, southafricanorth,
  October 2026).
  - Measured: about R0.00024 per tender.
  - Worst case: R0.20 × 2 runs × 30 days = **R12 a month**, and only if Cloudflare is down all month.
  - Normal use: close to R0.
- **Managed identity and the role assignment:** no charge.

## Also changed
`LOOKBACK_DAYS` is now 4, `CLOSING_SOON_DAYS` 5 and `MISSED_REPORT_DAYS` 5 (operator choice). A day that
fails to fetch now gets 10 runs to recover instead of 12. `tests/conftest.py` runs the hand-dated tests
with the values they were written for, so a future tuning edit doesn't break the suite.
