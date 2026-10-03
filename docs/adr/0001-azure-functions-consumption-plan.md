# ADR-0001: Host on Azure Functions Consumption plan (Linux, Python 3.11, South Africa North)

- **Status:** Accepted
- **Date:** 2026-09-17 (first deploy, `0ab3e0d`); recorded 2026-09-30
- **Requirements:** NFR-01, NFR-02

## Context
One user, two runs a day, a few minutes of work per run. Cost matters more than
anything else (IDEA_BRIEF SC8; A7). An always-on host would cost far more than the
product is worth.

## Decision
Run as an Azure Functions app on the **Consumption plan**: Linux, Python 3.11,
Functions runtime v4, region South Africa North (README.md:40-54).

## Consequences
- Near-zero cost: **US$0.068/month ≈ R1.09/month**, measured in Cost Management for 17–30 Sep 2026.
  The subscription bills in USD, and the figure is converted at Microsoft's R16.00 per US$. *(Corrected
  2026-09-30, OP-3: the earlier "R0.07/month" was the US-dollar figure mislabelled as rand.)* ADR-0026
  keeps the running cost at this level.
- A hard **10-minute `functionTimeout`** ceiling (`host.json:3`). Every run (fetch,
  scoring, email) must fit inside it, which drives ADR-0003's 420 s fetch budget.
- Cold starts don't matter for a timer job.
- The Functions storage account (`AzureWebJobsStorage`) already exists and is a
  candidate home for the run-to-run memory ADR-0012 and ADR-0013 need (Architect to decide).
- To verify (DevOps/Architect): the support timeline for Python 3.11 on Functions and
  for the Linux Consumption plan itself. Microsoft has signalled a move to Flex
  Consumption. I did not check current dates.
