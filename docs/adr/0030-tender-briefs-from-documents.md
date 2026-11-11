# ADR-0030: A short brief of each new approved tender, from its documents

- **Status:** Accepted (operator approval, 2026-11-11). Off until `BRIEFS_ENABLED=true`.
- **Date:** 2026-11-11
- **Code:** `documents.py`, `classify.briefs`, `pipeline.tender_documents`, `function_app._briefs`, `report.py` (email row, xlsx `brief`)

## Context
The digest shows a tender's title and a 600-character description. To decide whether to bid, the
operator needs the scope, duration, eligibility (CSD, tax, B-BBEE), briefing session and evaluation
method, which are in the tender documents.

**Survey of the 19 AI-approved tenders, 28 Oct to 11 Nov 2026** (58 files, 52 MB):
- **Access:** every link downloaded from `www.etenders.gov.za` with HTTP 200, without login, captcha or
  redirect.
- **Readable:**
  - 15 tenders had the scope in a readable PDF.
  - 3 had only scanned PDFs, which would need OCR.
  - 1 had only `.zip` folders, which are not read.
- **Where the scope sits:** on pages 2 to 21 of the main PDF, or in a separate file titled "Scope of
  work", "Terms of reference" or "Specification". Some files are noise, for example an "SAP Ariba
  Sourcing User Guide".
- **Live test of `documents.scope_text`:** 15 of 19 ok, about 4 s a tender, about 6,800 tokens of text
  per brief.

## Decision
- **Which tenders:** only **new** tenders the AI said YES to. That was about 1 to 2 a weekday in the
  survey, with at most 6 a run. A tender gets a brief in its first digest only, so nothing is stored.
- **Finding the text** (`documents.py`):
  - Try the files whose title says scope first, and skip help guides.
  - Read PDF (with `pypdf`, pure Python, BSD) and Word files.
  - Start at the first page that mentions the scope of work, and send at most 6,000 words.
- **Writing the brief:** Llama 3.3 70B on Cloudflare (context window 24,000 tokens, prompt plus
  answer), then gpt-5-nano with its own cap of R0.10 a run. It has at most 6 labelled lines: Scope,
  Deliverables, Duration, Requirements, Briefing, Evaluation, with "not stated" where the text says
  nothing.
- **Untrusted input:**
  - **Downloads:** https links on `www.etenders.gov.za` only, no redirects, 15 MB a file, 80 pages, 8
    files a tender, and Word files that unpack past 15 MB are refused. Nothing is written to disk, and
    a broken file is skipped, never raised.
  - **Prompt:** the document text sits inside `<document>` tags, and any such tag in the text is
    removed. The prompt says the text is untrusted data. The model has no tools.
  - **Output:** cut to 6 lines of 240 characters, HTML-escaped in the email, and stored as plain text
    in the xlsx.
- **Time:** every download and model call is clipped to `BRIEF_DEADLINE_S` (420 s into the run), so the
  run still fits the 10-minute timeout (`test_the_run_fits_the_function_timeout_in_the_worst_case`).
- **Email:** the brief appears under the title, with a footer warning to check the documents before
  bidding.

## Cost (ADR-0026)
- **Llama:** about 6,800 tokens in and up to 350 out per brief: about 180 + 70 = 250 neurons. At 2 a day
  that is about 500 neurons, on top of about 1,600 for classifying, of the free 10,000 a day. **R0.**
- **nano fallback:** about R0.008 a brief, capped at R0.10 a run. Worst case R2.20 a month (22 weekday
  runs), and only if Cloudflare is down every day.
- **Package and run time:** `pypdf` adds about 1 MB to the deployment. Downloads add a few seconds per
  tender.

## Not done
- **Scanned PDFs** (3 of 19) would need OCR, a separate service with its own cost.
- **`.zip` folders** (1 of 19).
- **Quality:** the brief has not been checked against a human reading of the same documents. The
  operator should compare the first few briefs with the documents.
