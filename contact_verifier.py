"""
Hunter.io contact finder: upgrades a generic address (info@, careers@) or a missing email
to a named person at the company, preferring tech/data/HR/hiring roles.
Only runs when HUNTER_API_KEY is set in .env.
"""
import re
import time

import requests
from config import config

DOMAIN_SEARCH_URL = "https://api.hunter.io/v2/domain-search"

# Job titles worth emailing, in rough priority order (first match wins the tie-break)
DECISION_KEYWORDS = [
    "cto", "chief technology", "cio", "chief information", "chief data", "head of data", "data",
    "analytics", "engineering manager", "engineering", "technical", "it manager", "it ", "operations",
    "electrical", "maintenance", "project manager", "hr", "human resources", "talent", "recruit",
    "people", "hiring",
]
_KEYWORD_RE = re.compile(r"\b(" + "|".join(re.escape(k.strip()) for k in DECISION_KEYWORDS) + r")", re.I)


def is_relevant_role(position):
    """True for tech/data/engineering/HR/hiring titles; False for e.g. Legal, Sales, Creative."""
    return bool(_KEYWORD_RE.search(position or ""))

GENERIC_LOCAL_PARTS = {
    "info", "contact", "hello", "support", "admin", "sales", "office",
    "enquiries", "inquiries", "careers", "career", "jobs", "job", "hr",
    "recruitment", "recruiting", "talent", "hiring", "people", "team",
    "no_email_found",
}


class HunterQuotaExceeded(Exception):
    """Raised when Hunter says the account is out of credits, so the batch can stop early."""


def is_generic_email(email):
    """True if the address looks like a team alias (info@, careers@) rather than a named person."""
    if not email or email == "no_email_found":
        return True
    local = email.split("@")[0].lower()
    if local in GENERIC_LOCAL_PARTS:
        return True
    if re.split(r"[._]", local)[0] in GENERIC_LOCAL_PARTS:  # info.gh@, sales_us@
        return True
    return not ("." in local or "_" in local)


class HunterClient:
    def __init__(self):
        self.api_key = config.get("HUNTER_API_KEY") or ""

    def is_configured(self):
        return bool(self.api_key) and "your_" not in self.api_key

    def domain_search(self, domain):
        """Returns a list of emails, or None if the lookup failed (so it can be retried later)."""
        params = {"domain": domain, "api_key": self.api_key, "type": "personal"}
        for attempt in range(3):
            try:
                resp = requests.get(DOMAIN_SEARCH_URL, params=params, timeout=20)
            except requests.RequestException as e:
                print(f"[Hunter] Request failed for {domain}: {e}")
                return None
            body = (resp.text or "").lower()
            if resp.status_code in (402, 403) or "usage limit" in body or "quota" in body:
                raise HunterQuotaExceeded(resp.text[:200])
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"[Hunter] Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                print(f"[Hunter] Domain search failed for {domain}: {resp.status_code} - {resp.text[:200]}")
                return None
            return resp.json().get("data", {}).get("emails", []) or []
        return None

    def best_contact(self, domain, min_confidence=50):
        """Returns a contact dict, {} when Hunter has no confident match, or None if the lookup failed."""
        emails = self.domain_search(domain)
        if emails is None:
            return None
        candidates = [e for e in emails if e.get("value")]
        if not candidates:
            return {}

        # Only people in a relevant role count. Better to keep careers@ than email a lawyer or salesperson.
        relevant = [e for e in candidates
                    if is_relevant_role(e.get("position")) and (e.get("confidence") or 0) >= min_confidence]
        if not relevant:
            return {}
        best = max(relevant, key=lambda e: e.get("confidence") or 0)
        return {
            "first_name": best.get("first_name") or "",
            "last_name": best.get("last_name") or "",
            "position": best.get("position") or "",
            "email": best["value"],
            "confidence": best.get("confidence") or 0,
        }
