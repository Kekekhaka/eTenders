# ADR-0028: The AI decides; keywords are the fallback

- **Status:** Accepted (operator decision, 2026-10-10). Supersedes the "second look" part of ADR-0027.
- **Date:** 2026-10-10
- **Code:** `pipeline._select`, `pipeline.ai_candidates`, `function_app._classify`, `report.py` (labels, second sheet)

## Context
In the digest of 9 October, 41 tenders were kept: 3 on keywords and 38 on the consulting-services
category alone.
- **The category rule was mostly noise:** diversity training, railway track maintenance,
  interpreting, farm valuations, armed response.
- **The keyword hits were weak:** `ai` matched AI-enabled cameras, and `azure` matched a cloud backup
  service.
- **The AI judged the 159 other tenders and said NO to all of them.** Spot-checking showed that was
  correct.

So the email was about 90% irrelevant to the operator's data profile.

## Decision
Every open tender goes to the AI step (same models, cap and time limit as ADR-0027).

| Situation | Result | `ai_flag` | Email label |
|---|---|---|---|
| AI says YES (Llama) | in the digest | Llama | none |
| AI says YES (nano) | in the digest | nano | AI: nano |
| AI says NO | out | | |
| AI says NO, but a keyword matched | out of the email; listed on the xlsx sheet "Keyword match, AI said no" | | |
| No verdict (both models failed, or time ran out) | in the digest if a keyword matched | not AI-checked | NOT AI-CHECKED |
| `LLM_ENABLED` off | the old rule: keyword hit or consulting category | blank | none |

- **The consulting category stops being a reason to keep a tender.** It is still sent to the AI as
  context.
- **Keyword score still orders the digest.**

## Consequences
- The email shrinks to the tenders the AI approves. On 9 October that would have been roughly 3–6
  instead of 41.
- **Cost:** about 200 tenders a day go to the AI instead of about 160. That is roughly 1,400
  neurons a day of the free 10,000. Nano stays capped at R0.20 a run. Expected cost: R0.
- **Risk:** the AI wrongly rejects a relevant tender.
  - The rejected keyword hits remain visible on the second sheet.
  - A tender with neither a keyword nor an AI YES is lost, as it was before.
  - The test sample had only 4 relevant tenders, so watch the second sheet for the first weeks.
- **Rollback:** set `LLM_ENABLED=false`. No deploy is needed.
