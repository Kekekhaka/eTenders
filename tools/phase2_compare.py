"""Phase 2 experiment: which model classifies tenders best, and what does it cost?

Step 1  python tools/phase2_compare.py sample 2026-09-28 2026-10-02 60
        or, when the API is down, from a downloaded bulk file:
        python tools/phase2_compare.py sample-file C:\\path\\01072026.json 60
        Writes tools/phase2_sample.csv. Open it in Excel and fill the "label"
        column with 1 (relevant to us) or 0 (not relevant) for every row.
Step 2  python tools/phase2_compare.py run
        Sends each labelled tender to every model whose settings are present and
        prints accuracy, misses and neurons used.

Settings come from environment variables; this script never prints them:
  CF_ACCOUNT_ID, CF_API_TOKEN                      Cloudflare Workers AI
  AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY,
  AZURE_OPENAI_DEPLOYMENT                          Azure gpt-5-nano (optional)
"""
import csv
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import time

import requests

try:  # on a network that re-signs HTTPS, trust the Windows certificate store
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import classify  # noqa: E402
import pipeline  # noqa: E402

SAMPLE = Path(__file__).with_name("phase2_sample.csv")
FIELDS = ["ocid", "title", "buyer", "category", "description", "label"]
CF_MODELS = [
    "@cf/meta/llama-3.1-8b-instruct-fp8-fast",
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
]
# A loose net, only used to put likely candidates into the sample.
LOOKS_DATA = re.compile(
    r"\b(data|database|sql|analytics?|dashboards?|reporting|business intelligence|"
    r"warehouse|etl|migration|software|information system|ict|digital|erp|sap|"
    r"licen[cs]es?|system development|application)\b", re.IGNORECASE)

PROMPT = classify.PROMPT  # the production prompt, so the test measures what runs


def sample(day_from: str, day_to: str, count: int) -> None:
    """Half the sample looks data or IT related (so it holds real positives and
    near misses); the other half is spread across everything else."""
    releases, _ = pipeline.fetch_releases(
        dt.date.fromisoformat(day_from), dt.date.fromisoformat(day_to))
    write_sample(releases, count)


def sample_file(path: str, count: int) -> None:
    """Same sample, drawn from a bulk file downloaded from the eTenders site."""
    with open(path, encoding="utf-8") as f:
        write_sample(json.load(f)["Releases"], count)


def write_sample(releases: list, count: int) -> None:
    likely, other, seen = [], [], set()
    for rel in releases:
        flat = pipeline.flatten(rel)
        if flat is None or flat["ocid"] in seen:
            continue
        seen.add(flat["ocid"])
        t = rel.get("tender") if isinstance(rel.get("tender"), dict) else {}
        row = {
            "ocid": flat["ocid"], "title": flat["title"] or "",
            "buyer": flat["buyer"] or "", "category": flat["category"] or "",
            "description": (t.get("description") or "")[:600], "label": "",
        }
        text = f"{row['title']} {row['description']}"
        (likely if flat["score"] > 0 or LOOKS_DATA.search(text) else other).append(row)
    # Rows already in the file keep their labels; new rows are added after them.
    old = []
    if SAMPLE.exists():
        with SAMPLE.open(newline="", encoding="utf-8-sig") as f:
            old = list(csv.DictReader(f))
    have = {r["ocid"] for r in old}
    new_likely = [r for r in likely if r["ocid"] not in have]
    new_other = [r for r in other if r["ocid"] not in have]
    room = max(0, count - len(old))
    picked = new_likely[:room]
    rest = room - len(picked)
    if rest > 0 and new_other:
        picked += new_other[::max(1, len(new_other) // rest)][:rest]
    for r in picked:  # no data/IT words at all: start at 0, skim and correct
        if r in new_other:
            r["label"] = "0"
    rows = old + picked
    print(f"{len(releases)} tenders, {len(likely)} look data/IT related; "
          f"kept {len(old)} existing rows, added {len(picked)}.")
    with SAMPLE.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    blank = sum(1 for r in rows if not r["label"].strip())
    print(f"Wrote {len(rows)} tenders to {SAMPLE}. {blank} still need a label: "
          f"1 relevant, 0 not, b borderline (left out of scoring).")


def ask_cloudflare(model: str, prompt: str) -> tuple[str, float]:
    url = (f"https://api.cloudflare.com/client/v4/accounts/"
           f"{os.environ['CF_ACCOUNT_ID']}/ai/run/{model}")
    r = requests.post(
        url, headers={"Authorization": f"Bearer {os.environ['CF_API_TOKEN']}"},
        json={"messages": [{"role": "user", "content": prompt}], "max_tokens": 5},
        timeout=60)
    r.raise_for_status()
    body = r.json()["result"]
    return str(body.get("response", "")), body.get("usage", {})


def ask_azure(prompt: str) -> tuple[str, dict]:
    url = (f"{os.environ['AZURE_OPENAI_ENDPOINT'].rstrip('/')}/openai/deployments/"
           f"{os.environ['AZURE_OPENAI_DEPLOYMENT']}/chat/completions"
           f"?api-version=2025-04-01-preview")
    for attempt in range(6):
        r = requests.post(
            url, headers={"api-key": os.environ["AZURE_OPENAI_KEY"]},
            json={"messages": [{"role": "user", "content": prompt}],
                  "max_completion_tokens": 400, "reasoning_effort": "minimal"},
            timeout=60)
        if r.status_code != 429:
            break
        time.sleep(min(int(r.headers.get("Retry-After", 5)), 30))  # rate limited: wait, retry
    r.raise_for_status()
    body = r.json()
    return body["choices"][0]["message"]["content"] or "", body.get("usage", {})


def available_models() -> dict:
    models = {}
    if os.environ.get("CF_ACCOUNT_ID") and os.environ.get("CF_API_TOKEN"):
        for m in CF_MODELS:
            models[m] = lambda p, m=m: ask_cloudflare(m, p)
    if os.environ.get("AZURE_OPENAI_KEY") and os.environ.get("AZURE_OPENAI_ENDPOINT"):
        models["azure gpt-5-nano"] = ask_azure
    if not models:
        sys.exit("No model settings found in the environment. See the top of this file.")
    return models


def show_answers() -> None:
    """No labels yet: print every model's YES/NO per tender, plus the keyword score."""
    with SAMPLE.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    models = available_models()
    print("  # | keywords | " + " | ".join(n.split("/")[-1][:14] for n in models) + " | description")
    errors = {}
    for i, r in enumerate(rows, 1):
        kw, _ = pipeline.score(f"{r['title']} {r['description']}")
        answers = []
        for name, ask in models.items():
            try:
                text, _ = ask(PROMPT.format(**r))
                answers.append("YES" if text.strip().upper().startswith("YES") else "no")
            except Exception as exc:
                answers.append("ERR")
                errors.setdefault((name, error_reason(exc)), 0)
                errors[(name, error_reason(exc))] += 1
        desc = " ".join((r["description"] or r["title"]).split())[:60]
        print(f"{i:3} | {'YES' if kw else 'no':8} | " + " | ".join(f"{a:14}" for a in answers) + f" | {desc}")
    for (name, reason), n in errors.items():
        print(f"\nERR x{n} for {name}: {reason}")


def error_reason(exc: Exception) -> str:
    """Status and start of the reply body; never the URL, which holds the account ID."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return type(exc).__name__
    return f"HTTP {resp.status_code} {resp.text[:200]}"


def run() -> None:
    with SAMPLE.open(newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r["label"].strip() in ("0", "1")]
    if not rows:
        print("No labels found: showing each model's answer instead of scoring.\n")
        return show_answers()

    models = {}
    if os.environ.get("CF_ACCOUNT_ID") and os.environ.get("CF_API_TOKEN"):
        for m in CF_MODELS:
            models[m] = lambda p, m=m: ask_cloudflare(m, p)
    if os.environ.get("AZURE_OPENAI_KEY") and os.environ.get("AZURE_OPENAI_ENDPOINT"):
        models["azure gpt-5-nano"] = ask_azure
    if not models:
        sys.exit("No model settings found in the environment. See the top of this file.")

    positives = sum(r["label"].strip() == "1" for r in rows)
    print(f"{len(rows)} labelled rows, {positives} relevant (borderline rows are left out).")
    # The keyword score is the baseline the models must beat.
    results = {"keywords": [(r, pipeline.score(f"{r['title']} {r['description']}")[0] > 0)
                            for r in rows]}
    tokens = {}
    for name, ask in models.items():
        answers = []
        tokens[name] = [0, 0]
        for r in rows:
            try:
                answer, usage = ask(PROMPT.format(**r))
            except Exception as exc:  # one bad call must not stop the comparison
                print(f"  {name}: call failed ({error_reason(exc)[:80]}); skipped")
                continue
            tokens[name][0] += usage.get("prompt_tokens", 0)
            tokens[name][1] += usage.get("completion_tokens", 0)
            answers.append((r, answer.strip().upper().startswith("YES")))
        results[name] = answers

    for name, answers in results.items():
        hit = [r for r, yes in answers if yes and r["label"].strip() == "1"]
        false_alarm = [r for r, yes in answers if yes and r["label"].strip() == "0"]
        missed = [r for r, yes in answers if not yes and r["label"].strip() == "1"]
        asked = len(answers)
        print(f"\n{name}  ({asked} answered)")
        print(f"  found {len(hit)} of {positives} relevant, "
              f"{len(false_alarm)} false alarms, {len(missed)} missed")
        if name in tokens:
            print(f"  tokens in {tokens[name][0]}, out {tokens[name][1]}")
        for tag, group in (("false alarm", false_alarm), ("missed", missed)):
            for r in group[:8]:
                print(f"    {tag}: {' '.join((r['description'] or r['title']).split())[:80]}")


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "sample":
        sample(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    elif len(sys.argv) == 4 and sys.argv[1] == "sample-file":
        sample_file(sys.argv[2], int(sys.argv[3]))
    elif len(sys.argv) == 2 and sys.argv[1] == "run":
        run()
    else:
        sys.exit(__doc__)
