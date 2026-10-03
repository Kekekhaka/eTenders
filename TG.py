"""Local runner: build the digest on this machine without sending email.

Writes the xlsx and an HTML preview of the email, with the same report.py
code the Function uses. Its memory is a local file (etenders_memory.json),
never the production blob: it never asks for a managed-identity token, so it
can't change what the Function remembers (FR-22).
"""

import datetime as dt

import memory
import pipeline
import report

MEMORY_FILE = "etenders_memory.json"
XLSX_FILE = "etenders_matches.xlsx"
HTML_FILE = "etenders_digest.html"


def main(memory_file: str = MEMORY_FILE, xlsx_file: str = XLSX_FILE,
         html_file: str = HTML_FILE) -> None:
    run_at = dt.datetime.now(dt.timezone.utc)
    store = memory.FileStore(memory_file)
    mem, status, etag = memory.load(store, run_at)
    print(f"Local memory: {status}")

    window = pipeline.window_for(run_at)
    print(f"Fetching releases published {window[0]} to {window[1]}...")
    releases, days = pipeline.fetch_releases(*window)
    missed = memory.missed_days(mem, window)
    d = pipeline.build_digest(releases, days, mem, status, "off", run_at, missed)

    for p in d.partial_days:
        print(f"  WARNING: {p.day} not fully fetched ({p.reason})")
    for m in d.missed_days:
        print(f"  MISSED: {m.day} ({m.kind.replace('_', ' ')}): check etenders.gov.za by hand")
    if d.dropped_releases:
        print(f"  {d.dropped_releases} malformed release(s) were left out")

    xlsx = report.build_xlsx(d)
    if xlsx is None:
        print("No matching tenders. Widen LOOKBACK_DAYS or CATEGORIES in pipeline.py.")
    else:
        with open(xlsx_file, "wb") as f:
            f.write(xlsx)
        print(f"{d.total} tenders ({d.new_count if d.new_count is not None else 'unknown'} new, "
              f"{d.closing_soon_count} closing soon) written to {xlsx_file}")
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(report.render_html(d))
    print(f"Email preview written to {html_file}")
    print(f"Subject would be: {report.render_subject(d)}")

    # Like the Function, remember only after the digest exists, and never
    # over a memory that could not be read.
    if status in ("ok", "first_run"):
        new_memory = memory.after_delivery(mem, d, days, run_at)
        print(f"Local memory saved: {memory.save(store, new_memory, etag)}")


if __name__ == "__main__":
    main()
