"""Make the brief for one tender now, from this laptop, to check a prompt change.

    python tools/brief_try.py 132/2026/EWSS/TRAINING/RFQ        # a tender number
    python tools/brief_try.py ocds-9t57fa-177791 14              # an ocid; look 14 days back

It finds the tender in the last N days of the eTenders API (default 10),
reads its documents the way the Function does (documents.tender_text), and
writes the brief with the production prompts (classify.briefs): a summary,
then every requirement and the evaluation, then the documents' checklist.
Needs CF_ACCOUNT_ID and CF_API_TOKEN in the environment; nano is not used.
Prints the brief and what was sent, never the token. Not deployed.
"""
import datetime as dt
import logging
import os
import sys
import time
from pathlib import Path

try:  # on a network that re-signs HTTPS, trust the Windows certificate store
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import classify  # noqa: E402
import documents  # noqa: E402
import pipeline  # noqa: E402


def main(key: str, days_back: int) -> None:
    if not (os.environ.get("CF_ACCOUNT_ID", "").strip() and os.environ.get("CF_API_TOKEN", "").strip()):
        sys.exit("Set CF_ACCOUNT_ID and CF_API_TOKEN in this terminal first, for example:\n"
                 '  $env:CF_ACCOUNT_ID = "your-account-id"\n  $env:CF_API_TOKEN = "your-token"')
    logging.disable(logging.WARNING)
    today = dt.date.today()
    releases, _ = pipeline.fetch_releases(today - dt.timedelta(days=days_back), today)
    match = [r for r in releases if isinstance(r, dict)
             and key.lower() in (r.get("ocid", "") + " " + str((r.get("tender") or {}).get("title", ""))).lower()]
    if not match:
        sys.exit(f"No tender matching {key!r} in the last {days_back} days.")
    rel = match[0]
    items = pipeline.tender_documents([rel], dt.datetime.now(dt.timezone.utc), [rel["ocid"]])
    if not items:
        sys.exit("The tender is closed.")
    item = items[0]
    print(f"{rel['ocid']} | {item['title']} | {len(item['docs'])} document(s)")

    sent = {}

    def fetch_text(it, deadline):
        texts, status = documents.tender_text(it["docs"], deadline)
        sent.update(texts=texts, status=status)
        return texts, status

    made, stats = classify.briefs([item], fetch_text, time.monotonic() + 300,
                                  get_token=lambda: (None, "not used here"))
    texts = sent.get("texts") or {}
    print(f"documents: {sent.get('status')}; words sent: summary {len((texts.get('summary') or '').split())}, "
          f"requirements {len((texts.get('requirements') or '').split())}")
    print(f"model: {stats}")
    print("\n" + (made.get(item["ocid"]) or "(no brief: see the status above)"))


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) == 3 else 10)
