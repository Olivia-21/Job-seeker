"""
Main orchestrator: search -> scrape -> verify -> generate -> (you approve) -> send.
Run `python outreach_pipeline.py` with no arguments for help.
"""
import os
import random
import smtplib
import ssl
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import urlparse

from config import config
from common import (
    LETTERS_DIR, STATUSES, NEEDS_CONTACT, load_profile, load_queue, save_queue, append_log,
    letter_path, cv_path, reject_reason, bare_domain, track_info, now_str,
)

PROFILE = load_profile()
LIMITS = PROFILE.get("limits", {})

_exa = _apify = _sender = _generator = None


def get_exa():
    global _exa
    if not _exa:
        from exa_search import ExaSearch
        _exa = ExaSearch()
    return _exa


def get_apify():
    global _apify
    if not _apify:
        from apify_scraper import ApifyScraper
        _apify = ApifyScraper()
    return _apify


def get_sender():
    global _sender
    if not _sender:
        from email_sender import EmailSender
        _sender = EmailSender(PROFILE)
    return _sender


def get_generator():
    global _generator
    if not _generator:
        from cover_letter_generator import CoverLetterGenerator
        _generator = CoverLetterGenerator(PROFILE)
    return _generator


# ---------- Queue ----------

def add_to_queue(company_name, website="", email="", track="data_engineer", recipient="Hiring Team",
                 address="", source_query="", queue=None, quiet=False):
    """Adds a lead unless it's a duplicate or fails the lead filters. Returns True if added."""
    own_queue = queue is None
    queue = load_queue() if own_queue else queue

    reason = reject_reason(PROFILE, company_name, website, email)
    if reason:
        if not quiet:
            print(f"[Queue] Rejected '{company_name}': {reason}")
        return False

    name_l = company_name.strip().lower()
    dom = bare_domain(website)
    for row in queue:
        if row["company_name"].strip().lower() == name_l:
            return False
        if dom and bare_domain(row["website"]) == dom:
            return False

    if track not in PROFILE["tracks"]:
        track = next(iter(PROFILE["tracks"]))
    queue.append({
        "company_name": company_name.strip(),
        "website": website,
        "email": email,
        "track": track,
        "role_title": track_info(PROFILE, track)["role_title"],
        "recipient_name": recipient,
        "company_address": address,
        "status": "Pending",
        "date_added": datetime.now().strftime("%Y-%m-%d"),
        "date_sent": "", "date_followup": "", "message_id": "",
        "source_query": source_query, "notes": "",
    })
    if own_queue:
        save_queue(queue)
    print(f"[Queue] Added: {company_name} ({track})")
    return True


# ---------- Phase 1: Discover ----------

def all_queries():
    """
    (track, query) pairs, interleaved across tracks: 1st query of every track, then the 2nd, ...
    Prime tracks get their queries twice per cycle; 'plus' tracks once.
    """
    per_track = []
    for key, t in PROFILE["tracks"].items():
        qs = t.get("queries", [])
        if t.get("priority") == "prime":
            qs = qs + qs
        per_track.append([(key, q) for q in qs])
    out = []
    for i in range(max((len(x) for x in per_track), default=0)):
        for x in per_track:
            if i < len(x):
                out.append(x[i])
    return out


def queries_for_today(n, day=None):
    """Round-robin through every query so all roles (IT, research, ...) get searched in turn."""
    pairs = all_queries()
    if not pairs:
        return []
    day = day or datetime.now().date()
    start = (day.toordinal() * n) % len(pairs)
    picked, seen = [], set()
    i = 0
    while len(picked) < n and i < len(pairs):
        track, q = pairs[(start + i) % len(pairs)]
        if q not in seen:
            picked.append((track, q))
            seen.add(q)
        i += 1
    return picked


def country_for(query):
    """Exa userLocation hint: only for queries that are about Ghana."""
    q = query.lower()
    return "GH" if any(w in q for w in ("ghana", "accra", "kumasi", "tema", "takoradi")) else None


def run_exa_search(query_str, track="data_engineer"):
    print(f"[Search] ({track}) '{query_str}'")
    employers = get_exa().find_employers(query_str, country_code=country_for(query_str))
    queue = load_queue()
    added = 0
    for c in employers:
        address = c["location"] if c["location"] and c["location"].lower() != "remote" else ""
        if add_to_queue(c["name"], c["website"], "", track, address=address, source_query=query_str, queue=queue):
            row = queue[-1]
            for k in ("source_url", "source_title", "source_snippet", "skills"):
                row[k] = c.get(k, "")
            row["job_title"], row["job_location"] = c["role"], c["location"]
            added += 1
    save_queue(queue)
    print(f"[Search] Added {added} new companies.")
    return added


# ---------- Phase 2: Enrich ----------

def run_apify_scrape(batch_size=None):
    batch_size = batch_size or int(LIMITS.get("scrape_batch", 10))
    queue = load_queue()
    pending = [r for r in queue if r["status"] == "Pending" and not r["email"]]
    if not pending:
        print("[Scrape] Nothing needs scraping.")
        return
    print(f"[Scrape] {len(pending)} companies need an email; crawling up to {batch_size}.")

    to_crawl = []
    for r in pending[:batch_size]:
        web = r["website"]
        if not web:
            results = get_exa().search_companies(f"{r['company_name']} official website homepage", num_results=1)
            if not results:
                r["status"], r["email"] = NEEDS_CONTACT, "no_email_found"
                r["notes"] = (r.get("notes", "") + " | No website found").strip(" |")
                print(f"[Scrape] No website for '{r['company_name']}' -> {NEEDS_CONTACT}.")
                continue
            parsed = urlparse(results[0]["url"])
            web = f"{parsed.scheme}://{parsed.netloc}"
            r["website"] = web
            reason = reject_reason(PROFILE, r["company_name"], web)
            if reason:
                r["status"], r["notes"] = "Skipped", reason
                print(f"[Scrape] Skipping '{r['company_name']}': {reason}")
                continue
        to_crawl.append(r)

    if not to_crawl:
        save_queue(queue)
        return

    scraped = get_apify().run_contact_scraper([r["website"] for r in to_crawl], max_pages=8, depth=1)
    emails_by_domain = {}
    for item in scraped:
        dom = bare_domain(item.get("domain") or item.get("originalStartUrl") or item.get("url", ""))
        valid = [e for e in item.get("emails", []) if "@" in e and "." in e.split("@")[1]
                 and not e.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"))]
        if dom and valid:
            emails_by_domain.setdefault(dom, []).extend(valid)

    updated = 0
    for r in to_crawl:
        dom = bare_domain(r["website"])
        found = []
        for d, ems in emails_by_domain.items():
            if d == dom or d.endswith("." + dom) or dom.endswith("." + d):
                found.extend(ems)
        if not found:
            r["email"], r["status"] = "no_email_found", NEEDS_CONTACT
            r["notes"] = (r.get("notes", "") + " | Website checked, no email found").strip(" |")
            print(f"[Scrape] No email on {r['website']} for '{r['company_name']}' -> {NEEDS_CONTACT}.")
            continue
        preferred = ["career", "job", "recruit", "hire", "talent", "hr", "people", "contact", "info"]
        primary = next((e for k in preferred for e in found if k in e.lower()), found[0])
        r["email"] = primary
        updated += 1
        print(f"[Scrape] {r['company_name']}: {primary}")
    save_queue(queue)
    print(f"[Scrape] Found emails for {updated} of {len(to_crawl)}.")


def run_verify_contacts(batch_size=None):
    """Hunter.io: swap info@/careers@ (or no email) for a named person. Skips quietly without a key."""
    from contact_verifier import HunterClient, HunterQuotaExceeded, is_generic_email
    hunter = HunterClient()
    if not hunter.is_configured():
        print("[Verify] HUNTER_API_KEY not set, skipping.")
        return
    batch_size = batch_size or int(LIMITS.get("verify_batch", 10))
    queue = load_queue()
    targets = [
        r for r in queue if r.get("website") and not r.get("hunter_checked") and (
            (r["status"] in ("Pending", "Approved") and is_generic_email(r.get("email", "")))
            or (r["status"] == NEEDS_CONTACT and r.get("email") in ("", "no_email_found"))
        )
    ][:batch_size]
    if not targets:
        print("[Verify] No contacts to upgrade.")
        return
    print(f"[Verify] Checking Hunter for {len(targets)} companies (max {batch_size} per run to save credits)...")
    upgraded = 0
    for i, r in enumerate(targets):
        dom = bare_domain(r["website"])
        if not dom:
            continue
        if i:
            time.sleep(1.5)
        try:
            c = hunter.best_contact(dom)
        except HunterQuotaExceeded as e:
            print(f"[Verify] Hunter credits used up for now ({e}). Stopping; the rest will be tried next run.")
            break
        if c is None:
            print(f"[Verify] {r['company_name']}: lookup failed, will retry next run.")
            continue
        r["hunter_checked"] = datetime.now().strftime("%Y-%m-%d")
        if not c:
            print(f"[Verify] {r['company_name']}: no confident named contact.")
            continue
        r["email"] = c["email"]
        full = f"{c['first_name']} {c['last_name']}".strip()
        if full:
            r["recipient_name"] = full
        if r["status"] == NEEDS_CONTACT:
            r["status"] = "Pending"
        if c["position"]:
            r["notes"] = (r.get("notes", "") + f" | Contact: {c['position']}").strip(" |")
        upgraded += 1
        print(f"[Verify] {r['company_name']}: {full or '?'} <{c['email']}> ({c['position'] or 'n/a'}, {c['confidence']}%)")
    save_queue(queue)
    print(f"[Verify] Upgraded {upgraded} of {len(targets)}.")


# ---------- Phase 3: Generate & send ----------

def generate_letter(r):
    os.makedirs(LETTERS_DIR, exist_ok=True)
    path = letter_path(PROFILE, r["company_name"])
    get_generator().generate_pdf(
        company_name=r["company_name"], track_key=r["track"], recipient_name=r["recipient_name"],
        company_address=r["company_address"], output_path=path, role_title=r.get("role_title") or None,
    )
    return path


def run_generate_letters(force=False, only=None):
    """Creates letters that don't exist yet (or all of them with force=True, or just `only` companies)."""
    queue = load_queue()
    rows = [r for r in queue if r["email"] and r["email"] != "no_email_found" and r["status"] in ("Pending", "Approved")]
    if only:
        wanted = {n.strip().lower() for n in only}
        rows = [r for r in queue if r["company_name"].lower() in wanted]
        force = True
    made = 0
    for r in rows:
        if force or not os.path.exists(letter_path(PROFILE, r["company_name"])):
            generate_letter(r)
            made += 1
    print(f"[Generate] {made} new/updated letters ({len(rows)} companies need one) in {LETTERS_DIR}")


def run_send_emails():
    queue = load_queue()
    approved = [r for r in queue if r["status"] == "Approved" and r["email"] and r["email"] != "no_email_found"]
    if not approved:
        print("[Send] Nothing is 'Approved'. Approve leads in the dashboard (or the CSV) first.")
        return
    cap = int(LIMITS.get("daily_send_cap", 15))
    sent_today = sum(1 for r in queue if r.get("date_sent", "").startswith(datetime.now().strftime("%Y-%m-%d")))
    room = max(cap - sent_today, 0)
    if room == 0:
        print(f"[Send] Daily cap of {cap} already reached today.")
        return
    to_send = approved[:room]
    cv = cv_path(PROFILE)
    if not os.path.exists(cv):
        print(f"[Send] CV not found at {cv}. Put your CV PDF there or fix cv_file in profile.json.")
        return
    print(f"[Send] Sending {len(to_send)} of {len(approved)} approved (cap {cap}/day, {sent_today} already today).")

    sender = get_sender()
    sent = failed = 0
    for i, r in enumerate(to_send):
        path = generate_letter(r)  # always fresh: today's date and the current role track
        msg_id = sender.send_outreach(r, path, cv)
        if msg_id:
            r["status"], r["date_sent"], r["message_id"] = "Sent", now_str(), msg_id
            append_log(r, "outreach", "SUCCESS", os.path.basename(path))
            sent += 1
        else:
            r["status"] = "Failed"
            append_log(r, "outreach", "FAILED", os.path.basename(path))
            failed += 1
        save_queue(queue)
        if i < len(to_send) - 1:
            delay = random.randint(30, 60)
            print(f"[Send] Waiting {delay}s...")
            time.sleep(delay)
    print(f"[Send] Done. Sent {sent}, failed {failed}.")


def run_test_send(track="data_engineer"):
    """Sends one sample application to YOUR OWN address. Never touches the queue."""
    if track not in PROFILE["tracks"]:
        print(f"Unknown track '{track}'. Use one of: {', '.join(PROFILE['tracks'])}")
        return
    sender = get_sender()
    me = sender.sender_email
    row = {
        "company_name": "Test Company Ltd", "email": me, "track": track,
        "role_title": track_info(PROFILE, track)["role_title"], "recipient_name": "Hiring Team",
        "company_address": "Accra, Ghana",
    }
    os.makedirs(LETTERS_DIR, exist_ok=True)
    path = os.path.join(LETTERS_DIR, f"TEST_{track}.pdf")
    get_generator().generate_pdf(company_name=row["company_name"], track_key=track,
                                 recipient_name=row["recipient_name"], company_address=row["company_address"],
                                 output_path=path)
    cv = cv_path(PROFILE)
    if not os.path.exists(cv):
        print(f"[Test] CV not found at {cv}.")
        return
    if sender.send_outreach(row, path, cv):
        print(f"[Test] Sent a sample {PROFILE['tracks'][track]['label']} application to {me}. Check your inbox.")
    else:
        print("[Test] Send failed - check GMAIL_APP_PASSWORD in .env.")


def run_find_postings(limit=None, recheck_days=7):
    """
    Finds the direct job posting for companies without one (or with only a homepage/general link).
    One Exa search per company; each company is re-checked at most once a week.
    """
    from exa_search import match_source, is_job_posting
    limit = limit or int(LIMITS.get("posting_lookups_per_run", 15))
    today = datetime.now().date()

    def due(r):
        if r["status"] in ("Skipped", "Rejected", "Offer", "Bounced"):
            return False
        if r.get("source_url") and is_job_posting(r["source_url"]):
            return False
        last = r.get("posting_checked", "")
        try:
            return (today - datetime.strptime(last, "%Y-%m-%d").date()).days >= recheck_days
        except ValueError:
            return True

    queue = load_queue()
    todo = [r for r in queue if due(r)][:limit]
    if not todo:
        print("[Postings] Every active company already has a job posting link.")
        return
    print(f"[Postings] Looking for job postings for {len(todo)} companies...")
    exa = get_exa()
    found = 0
    for r in todo:
        role = r.get("job_title") or r.get("role_title") or track_info(PROFILE, r["track"])["role_title"]
        where = r.get("job_location") or ""
        results = exa.search_companies(f"{r['company_name']} {role} job vacancy {where}".strip())
        src = match_source(r["company_name"], "", results)
        r["posting_checked"] = today.isoformat()
        if src["source_url"] and (is_job_posting(src["source_url"]) or not r.get("source_url")):
            r.update(src)
            found += 1
            print(f"[Postings] {r['company_name']}: {src['source_url']}")
        else:
            print(f"[Postings] {r['company_name']}: no posting found.")
    save_queue(queue)
    print(f"[Postings] Linked {found} of {len(todo)}.")


run_backfill_sources = run_find_postings  # old command name


def run_refilter():
    """Re-applies lead filters to companies already in the queue (e.g. after editing profile.json)."""
    queue = load_queue()
    changed = 0
    for r in queue:
        if r["status"] not in ("Pending", "Approved", NEEDS_CONTACT):
            continue
        reason = reject_reason(PROFILE, r["company_name"], r["website"], r["email"])
        if reason:
            r["status"] = "Skipped"
            r["notes"] = (r.get("notes", "") + f" | Auto-skipped: {reason}").strip(" |")
            changed += 1
            print(f"[Refilter] Skipped {r['company_name']}: {reason}")
    save_queue(queue)
    print(f"[Refilter] {changed} companies skipped.")


def set_status(company_name, status):
    if status not in STATUSES:
        print(f"Unknown status '{status}'. Use one of: {', '.join(STATUSES)}")
        return
    queue = load_queue()
    for r in queue:
        if r["company_name"].lower() == company_name.lower():
            r["status"] = status
            save_queue(queue)
            print(f"[Queue] {r['company_name']} -> {status}")
            return
    print(f"No company named '{company_name}' in the queue.")


def show_status():
    queue = load_queue()
    counts = {}
    for r in queue:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"\n--- Queue: {len(queue)} companies ---")
    for s in STATUSES:
        if counts.get(s):
            print(f"  {s:<14} {counts[s]}")
    by_track = {}
    for r in queue:
        by_track[r["track"]] = by_track.get(r["track"], 0) + 1
    print("\n  By role track:")
    for k, t in PROFILE["tracks"].items():
        print(f"  {t['label']:<22} {by_track.get(k, 0)}")
    print()


# ---------- Daily unattended run ----------

def send_daily_digest(lines):
    sender_email = config.get("SENDER_EMAIL") or PROFILE["email"]
    password = config.get("GMAIL_APP_PASSWORD")
    if not password:
        print("[Digest] GMAIL_APP_PASSWORD missing, skipping digest email.")
        return
    msg = EmailMessage()
    msg["From"] = sender_email
    msg["To"] = sender_email
    msg["Subject"] = f"Job Seeker - Daily Digest ({datetime.now():%Y-%m-%d})"
    msg.set_content("\n".join(lines))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as smtp:
            smtp.login(sender_email, password)
            smtp.send_message(msg)
        print("[Digest] Sent.")
    except Exception as e:
        print(f"[Digest] Failed: {e}")


def run_daily():
    """Discover + scrape + verify + generate. Never sends: approving and sending stays with you."""
    before = {r["company_name"].lower() for r in load_queue()}
    picks = queries_for_today(int(LIMITS.get("daily_search_queries", 2)))
    for track, q in picks:
        run_exa_search(q, track=track)
    run_apify_scrape()
    run_verify_contacts()
    try:
        run_find_postings()
    except Exception as e:  # never let a lookup problem stop the daily run
        print(f"[Postings] Skipped: {e}")
    run_generate_letters()

    after = load_queue()
    new_rows = [r for r in after if r["company_name"].lower() not in before]
    counts = {}
    for r in after:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    lines = [f"Daily prep run - {datetime.now():%Y-%m-%d %H:%M}", "Queries used:"]
    lines += [f'  [{PROFILE["tracks"][t]["label"]}] "{q}"' for t, q in picks]
    lines += ["", f"New companies today: {len(new_rows)}"]
    for r in new_rows:
        em = r["email"] if r["email"] and r["email"] != "no_email_found" else "no email yet"
        lines.append(f"  - {r['company_name']} [{PROFILE['tracks'].get(r['track'], {}).get('label', r['track'])}] {r['status']} - {em}")
        if r.get("source_url"):
            lines.append(f"      listing: {r['source_url']}")
    needs = [r for r in after if r["status"] == NEEDS_CONTACT]
    if needs:
        lines += ["", f"Needs contact (website checked, no email): {len(needs)}"]
        lines += [f"  - {r['company_name']} {r.get('website') or ''}" for r in needs[:15]]
    lines += ["", "Queue totals:"] + [f"  {s}: {counts[s]}" for s in STATUSES if counts.get(s)]
    lines += ["", "Open your dashboard, review the listings, approve the good ones and click Send."]
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## Daily Outreach Prep\n\n```\n" + text + "\n```\n")
    send_daily_digest(lines)


HELP = f"""
Usage: python outreach_pipeline.py <command>

  search "<query>" [track]           Find companies (track: {', '.join(PROFILE['tracks'])})
  add "<company>" <website> [email] [track]   Add a company by hand
  scrape                             Crawl Pending companies' sites for contact emails (Apify)
  verify                             Swap info@/careers@ for a named person via Hunter.io
  generate ["A|B"] [--force]         Build missing cover letters (or just companies A and B, or all with --force)
  approve "<company>"                Mark a company Approved (or use the dashboard)
  skip "<company>"                   Mark a company Skipped
  test-send [track]                  Email a sample application to YOURSELF (safe test)
  send                               Email Approved companies (cap {LIMITS.get('daily_send_cap', 15)}/day)
  backfill-sources                   Find the direct job posting link for companies missing one (Exa)
  refilter                           Re-apply lead filters to companies already in the queue
  status                             Queue counts by status and role track
  queries                            List every search query and which one runs today
  daily                              Unattended search+scrape+verify+generate + digest email (never sends)

Follow-ups:  python followup_pipeline.py [run]
Replies:     python check_emails.py
Dashboard:   python dashboard.py
"""

if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0].lower() if args else ""
    if cmd == "search":
        q = args[1] if len(args) > 1 else PROFILE["tracks"]["data_engineer"]["queries"][0]
        tr = args[2] if len(args) > 2 else "data_engineer"
        run_exa_search(q, tr)
    elif cmd == "add" and len(args) >= 3:
        add_to_queue(args[1], args[2], args[3] if len(args) > 3 else "", args[4] if len(args) > 4 else "data_engineer")
    elif cmd == "scrape":
        run_apify_scrape()
    elif cmd == "verify":
        run_verify_contacts()
    elif cmd == "generate":
        names = [n for n in (args[1] if len(args) > 1 else "").split("|") if n.strip()]
        run_generate_letters(force="--force" in args, only=names or None)
    elif cmd == "approve" and len(args) > 1:
        set_status(args[1], "Approved")
    elif cmd == "skip" and len(args) > 1:
        set_status(args[1], "Skipped")
    elif cmd == "test-send":
        run_test_send(args[1] if len(args) > 1 else "data_engineer")
    elif cmd == "send":
        run_send_emails()
    elif cmd == "backfill-sources":
        run_backfill_sources()
    elif cmd == "refilter":
        run_refilter()
    elif cmd == "status":
        show_status()
    elif cmd == "queries":
        for t, q in sorted(set(all_queries())):
            print(f"  [{PROFILE['tracks'][t]['label']}] {q}")
        print("\nToday:", *[f'\n  {q}' for _, q in queries_for_today(int(LIMITS.get("daily_search_queries", 2)))])
    elif cmd == "daily":
        run_daily()
    else:
        print(HELP)
