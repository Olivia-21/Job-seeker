import json
import re
from urllib.parse import urlparse

import requests
from config import config

# Words that indicate a title is a job-posting/listing page rather than a company name
JOB_TITLE_KEYWORDS = [
    "engineer", "developer", "manager", "specialist", "officer", "analyst",
    "designer", "architect", "lead", "intern", "job", "jobs", "career", "careers",
    "hiring", "vacancy", "vacancies", "position",
]

TITLE_SEPARATOR = re.compile(r"\s+[-|·•—–]\s+")
TRAILING_COMPANY = re.compile(r"\bat\s+([A-Z][\w&.,'\- ]{1,60})$")


def _looks_like_job_posting(text):
    lower = text.lower()
    return any(keyword in lower for keyword in JOB_TITLE_KEYWORDS)


def clean_company_name(raw_title):
    """
    Best-effort extraction of a company name from an arbitrary page title.
    Search results are often job-posting or job-board pages (e.g. "Frontend
    Developer - Fintech jobs at Acme"), not company homepages, so a naive
    first-segment split picks up the job title instead of the company.
    """
    title = (raw_title or "Unknown").strip()
    parts = [p.strip() for p in TITLE_SEPARATOR.split(title) if p.strip()]

    if len(parts) <= 1:
        if _looks_like_job_posting(title):
            match = TRAILING_COMPANY.search(title)
            if match:
                return match.group(1).strip(" -|·•—–")
        return title

    first = parts[0]
    if not _looks_like_job_posting(first):
        return first

    match = TRAILING_COMPANY.search(title)
    if match:
        return match.group(1).strip(" -|·•—–")

    return parts[-1]


def domain_looks_related(company_name, domain):
    """
    Heuristic check for whether `domain` plausibly belongs to `company_name`,
    used to avoid trusting a search result's domain when the page is actually
    a job board or aggregator mentioning the company rather than its own site
    (e.g. company_name="Kudi Systems" but domain="jobsearchgh.com").
    """
    if not domain:
        return False

    netloc = re.sub(r"^https?://", "", domain.lower()).split("/")[0]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    registrable = re.sub(r"[^a-z0-9]", "", netloc.split(".")[0])

    name_tokens = [t for t in re.findall(r"[a-z0-9]+", company_name.lower()) if len(t) >= 3]

    if not registrable or not name_tokens:
        return True  # not enough signal to judge either way, don't block on it

    if any(token in registrable or registrable in token for token in name_tokens):
        return True

    joined_name = re.sub(r"[^a-z0-9]", "", company_name.lower())
    return registrable in joined_name or joined_name in registrable


SEARCH_URL = "https://api.exa.ai/search"

# Rules for Exa's synthesized output. Keep/drop logic lives here, not in the query text
# (per Exa's build-with-exa guidance: query = what to retrieve, systemPrompt = what to keep).
EMPLOYER_SYSTEM_PROMPT = (
    "You are building a list of real employers for a job seeker. "
    "Include a company only when the pages show it is a real organisation that employs people in the "
    "role described in the query. Return the employer itself, never a job board, recruitment aggregator, "
    "staffing marketplace, news site, government body or university. "
    "For 'website', give the employer's own official homepage domain (for example https://acme.com), "
    "not the URL of the job listing or the page you read; leave it empty if you cannot confirm it. "
    "For 'source_url', copy the exact URL of the search-result page that is the job posting itself "
    "(the individual job ad with the description and requirements), preferring it over a general careers "
    "page or homepage; never build or guess a URL. "
    "For 'skills', list the key skills or requirements that page mentions, comma-separated and short. "
    "Never invent a company, website, URL or email. List each company at most once."
)

EMPLOYER_SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "website": {"type": "string"},
                    "role": {"type": "string"},
                    "location": {"type": "string"},
                    "source_url": {"type": "string"},
                    "skills": {"type": "string"},
                },
                "required": ["name"],
            },
        }
    },
    "required": ["companies"],
}


class ExaSearch:
    def __init__(self):
        self.api_key = config.get("EXA_API_KEY")
        if not self.api_key:
            raise ValueError("EXA_API_KEY is missing. Add it to .env (local) or the repo's Actions secrets.")

    def _post(self, payload):
        try:
            resp = requests.post(
                SEARCH_URL, json=payload, timeout=90,
                headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
            )
        except requests.RequestException as e:
            print(f"[Exa] Request failed: {e}")
            return None
        if resp.status_code != 200:
            print(f"[Exa] Search failed: {resp.status_code} - {resp.text[:500]}")
            return None
        data = resp.json()
        cost = (data.get("costDollars") or {}).get("total")
        if cost is not None:
            print(f"[Exa] Cost: ${cost}")
        return data

    def search_companies(self, query, num_results=None):
        """
        Plain search (recommended request: query + auto + highlights). Returns raw results with
        title/url/highlights. Used for lookups such as finding one company's homepage.
        """
        payload = {"query": query, "type": "auto", "contents": {"highlights": True}}
        if num_results is not None:
            payload["numResults"] = num_results  # intentional, e.g. 1 for a homepage lookup
        print(f"[Exa] Searching: '{query}'")
        data = self._post(payload)
        results = (data or {}).get("results", [])
        print(f"[Exa] {len(results)} results.")
        return results

    def find_employers(self, query, country_code=None):
        """
        Search + structured output: returns a list of
        {"name", "website", "role", "location"} extracted from the pages by Exa.
        Falls back to title parsing if Exa returns no structured output.
        """
        payload = {
            "query": query,
            "type": "auto",
            "systemPrompt": EMPLOYER_SYSTEM_PROMPT,
            "outputSchema": EMPLOYER_SCHEMA,
            "contents": {"highlights": True},
        }
        if country_code:
            payload["userLocation"] = country_code  # location-sensitive query (e.g. Accra -> GH)
        print(f"[Exa] Finding employers: '{query}'" + (f" [{country_code}]" if country_code else ""))
        data = self._post(payload)
        if not data:
            return []

        content = (data.get("output") or {}).get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except json.JSONDecodeError:
                content = None
        companies = (content or {}).get("companies") if isinstance(content, dict) else None

        results = list(data.get("results", []))
        # Citations from Exa's synthesized output are real pages too; add them to the pool we match against.
        known = {_norm_url(r.get("url")) for r in results}
        for u in _urls_in((data.get("output") or {}).get("grounding")):
            if _norm_url(u) not in known:
                results.append({"url": u, "title": "", "highlights": []})
                known.add(_norm_url(u))
        print(f"[Exa] {len(results)} pages in results/citations.")
        if companies:
            out = []
            for c in companies:
                name = (c.get("name") or "").strip()
                if not name:
                    continue
                src = match_source(name, c.get("source_url", ""), results)
                out.append({
                    "name": name,
                    "website": normalise_homepage(c.get("website", "")),
                    "role": (c.get("role") or "").strip(),
                    "location": (c.get("location") or "").strip(),
                    "skills": (c.get("skills") or "").strip()[:200],
                    **src,
                })
            linked = sum(1 for o in out if o["source_url"])
            print(f"[Exa] {len(out)} employers extracted ({linked} with a source listing link).")
            return out

        print("[Exa] No structured output returned; falling back to page titles.")
        out = []
        for r, raw in zip(self.get_company_domains(results), results):
            out.append({
                "name": r["name"],
                "website": r["domain"] if domain_looks_related(r["name"], r["domain"]) else "",
                "role": "", "location": "", "skills": "",
                "source_url": raw.get("url", ""), "source_title": (raw.get("title") or "")[:150],
                "source_snippet": _snippet(raw),
            })
        return out

    def get_company_domains(self, results):
        """Fallback: derive a company name and domain from raw result titles/URLs."""
        companies = []
        for r in results:
            parsed = urlparse(r.get("url", ""))
            companies.append({
                "name": clean_company_name(r.get("title", "Unknown")),
                "url": r.get("url", ""),
                "domain": f"{parsed.scheme}://{parsed.netloc}",
            })
        return companies


def normalise_homepage(url):
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _norm_url(u):
    return (u or "").strip().rstrip("/").lower().replace("://www.", "://")


def _snippet(result, limit=300):
    hl = result.get("highlights") or []
    text = " ".join(h.strip() for h in hl if isinstance(h, str)) or (result.get("text") or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("..." if len(text) > limit else "")


JOB_URL_HINTS = (
    "/job", "/jobs", "/career", "/careers", "/vacanc", "/opening", "/position", "/apply", "/recruit",
    "greenhouse.io", "lever.co", "workable.com", "smartrecruiters", "bamboohr", "recruitee", "breezy.hr",
    "myworkdayjobs", "linkedin.com/jobs", "indeed.", "glassdoor.", "jobberman", "myjobmag", "ghanajob",
    "jobsinghana", "wellfound", "remoteok", "himalayas", "weworkremotely", "remotive", "jobweb",
)
JOB_TITLE_HINTS = ("job", "vacanc", "hiring", "career", "apply", "position", "opening", "recruit")


def _urls_in(obj):
    """Every http(s) URL inside an arbitrary JSON structure (Exa grounding/citations)."""
    found = []
    if isinstance(obj, dict):
        for v in obj.values():
            found += _urls_in(v)
    elif isinstance(obj, list):
        for v in obj:
            found += _urls_in(v)
    elif isinstance(obj, str) and obj.startswith(("http://", "https://")):
        found.append(obj)
    return found


def job_score(url, title=""):
    """How much a page looks like an actual job posting (0 = homepage-ish)."""
    u, t = (url or "").lower(), (title or "").lower()
    score = sum(2 for h in JOB_URL_HINTS if h in u) + sum(1 for h in JOB_TITLE_HINTS if h in t)
    path = urlparse(u).path.strip("/")
    if path.count("/") >= 1:  # deeper path = more likely an individual ad than a landing page
        score += 1
    return score


def _company_score(company_name, result):
    tokens = [t for t in re.findall(r"[a-z0-9]+", company_name.lower()) if len(t) >= 3]
    hay = " ".join([result.get("title") or "", result.get("url") or "",
                    " ".join(h for h in (result.get("highlights") or []) if isinstance(h, str))]).lower()
    hits = sum(1 for t in tokens if t in hay)
    return hits, (hits >= max(1, len(tokens) // 2)) if tokens else False


def match_source(company_name, claimed_url, results):
    """
    Returns {"source_url", "source_title", "source_snippet"} for the job posting this company was found on.
    Only accepts URLs that Exa actually returned (results or citations), never a guessed URL, and prefers
    a real job ad over a homepage or general page.
    """
    by_url = {_norm_url(r.get("url")): r for r in results if r.get("url")}
    claimed = by_url.get(_norm_url(claimed_url))

    best, best_key = None, None
    for r in results:
        hits, ok = _company_score(company_name, r)
        if not ok:
            continue
        key = (job_score(r.get("url"), r.get("title")), hits)
        if best_key is None or key > best_key:
            best, best_key = r, key

    hit = claimed
    if best is not None and (claimed is None or job_score(best.get("url"), best.get("title")) >
                             job_score(claimed.get("url"), claimed.get("title"))):
        hit = best
    if not hit:
        return {"source_url": "", "source_title": "", "source_snippet": ""}
    return {"source_url": hit.get("url", ""), "source_title": (hit.get("title") or "")[:150],
            "source_snippet": _snippet(hit)}


def is_job_posting(url):
    return job_score(url) >= 2
