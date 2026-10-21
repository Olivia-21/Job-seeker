"""
Shared helpers: profile loading, queue/log file IO, lead filtering and file naming.
Every script imports from here so the CSV schema lives in exactly one place.
"""
import csv
import json
import os
import re
from datetime import datetime
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_FILE = os.path.join(BASE_DIR, "profile.json")
QUEUE_FILE = os.path.join(BASE_DIR, "outreach_queue.csv")
LOG_FILE = os.path.join(BASE_DIR, "outreach_log.csv")
REPLIES_LOG_FILE = os.path.join(BASE_DIR, "replies_log.csv")
LETTERS_DIR = os.path.join(BASE_DIR, "generated_letters")

QUEUE_FIELDS = [
    "company_name", "website", "email", "track", "role_title", "recipient_name",
    "company_address", "status", "date_added", "date_sent", "date_followup", "message_id", "hunter_checked", "posting_checked", "source_query", "job_title", "job_location", "source_url", "source_title",
    "source_snippet", "skills", "notes",
]
LOG_FIELDS = ["date", "company_name", "email", "track", "kind", "status", "cover_letter"]
REPLY_FIELDS = ["date_received", "company_name", "from_email", "subject", "message_id", "intent", "summary"]

# Pipeline order. Anything after "Sent" counts as contacted.
STATUSES = [
    "Pending", "Needs Contact", "Approved", "Sent", "Followup-Sent", "Replied",
    "Interview", "Offer", "Rejected", "Bounced", "Failed", "Skipped",
]
NEEDS_CONTACT = "Needs Contact"  # website checked, no usable email found: review by hand
CONTACTED_STATUSES = {"Sent", "Followup-Sent", "Replied", "Interview", "Offer", "Rejected", "Bounced"}


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_profile():
    with open(PROFILE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def track_info(profile, track_key):
    tracks = profile["tracks"]
    return tracks.get(track_key) or tracks[next(iter(tracks))]


# ---------- CSV IO ----------

def _read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def load_queue():
    rows = _read_csv(QUEUE_FILE)
    for r in rows:
        for k in QUEUE_FIELDS:
            r.setdefault(k, "")
            if r[k] is None:
                r[k] = ""
        if not r["status"]:
            r["status"] = "Pending"
        # Older runs parked "website checked, no email" companies under Skipped.
        if r["status"] == "Skipped" and r["email"] == "no_email_found" and "Auto-skipped" not in r["notes"]:
            r["status"] = NEEDS_CONTACT
        if r["status"] in ("Pending", "Approved") and r["email"] == "no_email_found":
            r["status"] = NEEDS_CONTACT
    return rows


def save_queue(queue):
    tmp = QUEUE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=QUEUE_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(queue)
    os.replace(tmp, QUEUE_FILE)  # atomic, so the dashboard never reads a half-written file


def load_log():
    return _read_csv(LOG_FILE)


def append_log(row, kind, status, letter_file=""):
    write_header = not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0
    with open(LOG_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "date": now_str(),
            "company_name": row["company_name"],
            "email": row["email"],
            "track": row.get("track", ""),
            "kind": kind,
            "status": status,
            "cover_letter": letter_file,
        })


def load_replies():
    return _read_csv(REPLIES_LOG_FILE)


# ---------- Naming ----------

def safe_name(company_name):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", company_name).strip("_") or "Company"


def letter_filename(profile, company_name):
    return f"{profile['file_prefix']}_Cover_Letter_{safe_name(company_name)}.pdf"


def letter_path(profile, company_name):
    return os.path.join(LETTERS_DIR, letter_filename(profile, company_name))


def cv_path(profile):
    return os.path.join(BASE_DIR, profile["cv_file"])


# ---------- Lead quality ----------

def bare_domain(url_or_email):
    s = (url_or_email or "").strip().lower()
    if "@" in s and "://" not in s:
        s = s.split("@", 1)[1]
    if "://" in s:
        s = urlparse(s).netloc
    s = s.split("/")[0]
    return s[4:] if s.startswith("www.") else s


def reject_reason(profile, company_name, website="", email=""):
    """
    Returns a short reason string if this lead should never enter the queue
    (job boards, government/university sites, place names posing as companies), else None.
    """
    filters = profile.get("lead_filters", {})
    name = (company_name or "").strip().lower()
    if len(name) < 2:
        return "empty name"
    if name in {n.lower() for n in filters.get("blocked_names", [])}:
        return f"'{company_name}' looks like a place/page, not a company"

    for excluded in filters.get("excluded_companies", []):
        if excluded.lower() in name:
            return f"'{company_name}' is on your excluded list"

    for d in (bare_domain(website), bare_domain(email)):
        if not d:
            continue
        if re.search(r"\.(gov|edu|ac|mil)(\.[a-z]{2})?$", d):
            return f"{d} is a government/education domain"
        for suffix in filters.get("blocked_domain_suffixes", []):
            if d.endswith(suffix):
                return f"{d} is a government/education domain"
        for blocked in filters.get("blocked_domains", []):
            if d == blocked or d.endswith("." + blocked):
                return f"{d} is a job board / aggregator"
    return None
