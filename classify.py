"""The AI step: an LLM decides which open tenders go in the digest (ADR-0028).

Each candidate goes to Llama 3.3 70B on Cloudflare Workers AI (free plan:
10,000 neurons a day, about 7 per tender). If Cloudflare fails or the
allowance runs out, the rest go to gpt-5-nano on Azure OpenAI, reached with
the Function's managed identity (no key), until this run has spent
NANO_CAP_ZAR. A tender neither model answered stays unclassified, falls
back to its keyword result, and is tried again next run. Verdicts are remembered
in the run memory ("ai"), so each tender is judged once.

Off unless LLM_ENABLED=true. Never raises; never logs a token, key, URL or
response body. The model choice and the numbers are from the phase 2 test
(tools/phase2_compare.py, October 2026).
"""

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

import memory
from digest import is_safe_url

logger = logging.getLogger(__name__)

CF_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
CF_MAX_FAILURES = 3        # after this many failed Cloudflare calls in a row, use nano for the rest
CF_ATTEMPTS = 2            # a timeout, connection error or HTTP 5xx is tried once more before it counts
NANO_API_VERSION = "2025-04-01-preview"
NANO_RESOURCE = "https://cognitiveservices.azure.com"
NANO_CAP_ZAR = 0.20        # most nano may cost in one run (a few calls may overshoot by < R0.01)
# Azure Retail Prices, gpt-5-nano Global, southafricanorth, October 2026.
NANO_ZAR_PER_M_IN, NANO_ZAR_PER_M_OUT = 0.80, 6.40
BUDGET_S = 240             # no new call starts after this; one run a day judges a whole day's tenders
# The longest one tender can take: Cloudflare CF_ATTEMPTS times, then nano twice plus a 429 wait.
MAX_CALL_S = CF_ATTEMPTS * 20 + 2 * 20 + 5   # 20 is CALL_TIMEOUT_S
WORKERS = 4                # parallel calls, kept low for Cloudflare's rate limit
CALL_TIMEOUT_S = 20
DESCRIPTION_CHARS = 600

PROMPT = (
    "You help a South African data professional find government tenders for "
    "their skills. Core: data engineering, SQL and database development, data "
    "warehousing, ETL and data migration, data governance and quality, business "
    "intelligence, reporting, dashboards and data analytics (Power BI, SSIS, "
    "Azure, Databricks, Python). Adjacent: software development, API "
    "integration, Power Apps and Power Automate, RPA, test automation, machine "
    "learning and AI, SAP reporting or data work. A tender is relevant only if "
    "the work itself is mainly one of those. It is NOT relevant if it is "
    "general IT hardware or device supply, networks, telecoms, physical "
    "security or guarding, construction, or non-IT services. "
    "Answer with one word: YES or NO.\n\n"
    "Title: {title}\nBuyer: {buyer}\nCategory: {category}\n"
    "Description: {description}"
)


def enabled() -> bool:
    return os.environ.get("LLM_ENABLED", "").strip().lower() == "true"


def _answer(text) -> bool | None:
    """True for YES, False for NO, None for anything else."""
    word = str(text or "").strip().upper()
    if word.startswith("YES"):
        return True
    if word.startswith("NO"):
        return False
    return None


class _Run:
    """The shared state of one classify() call; every change holds the lock."""

    def __init__(self, http, get_token):
        self.http = http
        self.lock = threading.Lock()
        self.cf_account = os.environ.get("CF_ACCOUNT_ID", "").strip()
        self.cf_token = os.environ.get("CF_API_TOKEN", "").strip()
        self.cf_failures = 0   # calls that failed, for RUN_SUMMARY
        self.cf_streak = 0     # failed calls in a row
        self.cf_off = None if self.cf_account and self.cf_token else "not_configured"
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
        deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5-nano").strip()
        self.nano_url = (f"{endpoint}/openai/deployments/{deployment}/chat/completions"
                         f"?api-version={NANO_API_VERSION}")
        self.nano_off = None if endpoint.startswith("https://") and is_safe_url(self.nano_url) \
            else "not_configured"
        self._get_token = get_token
        self._nano_token = None
        self.nano_zar = 0.0
        self.counts = {"llama": 0, "nano": 0}

    # --- Cloudflare ----------------------------------------------------------
    def _cf_succeeded(self) -> None:
        with self.lock:
            self.cf_streak = 0  # only failures in a row switch Cloudflare off

    def _cf_failed(self, reason: str) -> None:
        with self.lock:
            self.cf_failures += 1
            self.cf_streak += 1
            # 401/403: bad token; 429: allowance or rate limit. No point retrying.
            if self.cf_streak >= CF_MAX_FAILURES or reason in ("http_401", "http_403", "http_429"):
                if self.cf_off is None:
                    logger.warning("Cloudflare is off for the rest of this run: %s", reason)
                self.cf_off = reason

    def ask_cloudflare(self, prompt: str) -> bool | None:
        if self.cf_off:
            return None
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.cf_account}/ai/run/{CF_MODEL}"
        reason = "unknown"
        for attempt in range(CF_ATTEMPTS):
            try:
                r = self.http.post(url, headers={"Authorization": f"Bearer {self.cf_token}"},
                                   json={"messages": [{"role": "user", "content": prompt}],
                                         "max_tokens": 5},
                                   timeout=CALL_TIMEOUT_S)
                if r.status_code == 200:
                    text = r.json()["result"]["response"]
                    self._cf_succeeded()
                    return _answer(text)
                reason = f"http_{r.status_code}"
                if r.status_code < 500:
                    break  # a 4xx will not change on a second try
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                reason = type(exc).__name__
            if self.cf_off:
                return None  # another tender already switched Cloudflare off
        self._cf_failed(reason)
        return None

    # --- Azure gpt-5-nano ----------------------------------------------------
    def _token(self) -> str | None:
        with self.lock:
            if self._nano_token is None and self.nano_off is None:
                token, status = self._get_token()
                if token is None:
                    logger.warning("No managed identity token for Azure OpenAI: %s", status)
                    self.nano_off = f"token:{status}"
                self._nano_token = token
            return self._nano_token

    def ask_nano(self, prompt: str) -> bool | None:
        with self.lock:
            if self.nano_off or self.nano_zar >= NANO_CAP_ZAR:
                return None
        token = self._token()
        if token is None:
            return None
        body = {"messages": [{"role": "user", "content": prompt}],
                "max_completion_tokens": 400, "reasoning_effort": "minimal"}
        try:
            for attempt in range(2):
                r = self.http.post(self.nano_url, headers={"Authorization": f"Bearer {token}"},
                                   json=body, timeout=CALL_TIMEOUT_S)
                if r.status_code != 429 or attempt:
                    break
                time.sleep(5)  # rate limited: one short wait, then one retry
            if r.status_code != 200:
                return None
            reply = r.json()
            text = reply["choices"][0]["message"]["content"]
            usage = reply.get("usage") or {}
            cost = (usage.get("prompt_tokens", 0) * NANO_ZAR_PER_M_IN
                    + usage.get("completion_tokens", 0) * NANO_ZAR_PER_M_OUT) / 1_000_000
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
            return None
        with self.lock:
            self.nano_zar += cost
        return _answer(text)

    # --- one tender ----------------------------------------------------------
    def judge(self, tender: dict, deadline: float) -> tuple[str, bool, str] | None:
        """(ocid, relevant, model) or None when no model answered in time."""
        if time.monotonic() >= deadline:
            return None
        prompt = PROMPT.format(
            title=tender.get("title") or "", buyer=tender.get("buyer") or "",
            category=tender.get("category") or "",
            description=(tender.get("description") or "")[:DESCRIPTION_CHARS])
        for model, ask in (("llama", self.ask_cloudflare), ("nano", self.ask_nano)):
            if time.monotonic() >= deadline:
                return None
            verdict = ask(prompt)
            if verdict is not None:
                with self.lock:
                    self.counts[model] += 1
                return tender["ocid"], verdict, model
        return None


def classify(candidates: list[dict], *, http=requests, get_token=None,
             budget_s: float = BUDGET_S) -> tuple[dict, dict]:
    """Judge each candidate (dicts with ocid, title, buyer, category,
    description). Returns (verdicts, stats): verdicts maps ocid to
    {"v": relevant, "by": "llama" | "nano"} for the tenders a model answered;
    stats is for RUN_SUMMARY. Never raises."""
    if get_token is None:
        def get_token():
            return memory.mi_token(NANO_RESOURCE)
    run = _Run(http, get_token)
    deadline = time.monotonic() + budget_s
    results = []
    if candidates and not (run.cf_off and run.nano_off):
        try:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                results = list(pool.map(lambda t: run.judge(t, deadline), candidates))
        except Exception as exc:  # a bug here must never stop the digest
            logger.error("AI classification failed: %s", type(exc).__name__)
            results = []
    verdicts = {ocid: {"v": relevant, "by": model}
                for ocid, relevant, model in filter(None, results)}
    stats = {
        "candidates": len(candidates), "llama": run.counts["llama"], "nano": run.counts["nano"],
        "unclassified": len(candidates) - len(verdicts),
        "flagged": sum(v["v"] for v in verdicts.values()),
        "cf_failures": run.cf_failures, "cf_off": run.cf_off, "nano_off": run.nano_off,
        "nano_zar": round(run.nano_zar, 4),
    }
    return verdicts, stats
