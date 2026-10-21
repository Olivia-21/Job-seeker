import os
import csv
import re
import json
import ssl
import imaplib
import email
from email.header import decode_header
from email.utils import parseaddr
from datetime import datetime
import requests
from config import config

from common import load_queue, save_queue, REPLIES_LOG_FILE, REPLY_FIELDS, now_str

# Flash-Lite is fast and cheap, which fits a short classify-and-summarize task like this.
GEMINI_MODEL = "gemini-2.5-flash-lite"

AUTO_REPLY_SUBJECT_KEYWORDS = [
    "out of office", "automatic reply", "auto-reply", "autoreply",
    "delivery status notification", "undeliverable", "mail delivery failed",
]
BOUNCE_SENDER_KEYWORDS = ["mailer-daemon", "postmaster"]


def load_seen_message_ids():
    if not os.path.exists(REPLIES_LOG_FILE):
        return set()
    with open(REPLIES_LOG_FILE, "r", encoding="utf-8", newline="") as f:
        return {row["message_id"] for row in csv.DictReader(f)}


def log_reply(entry):
    fields = REPLY_FIELDS
    write_header = not os.path.exists(REPLIES_LOG_FILE) or os.path.getsize(REPLIES_LOG_FILE) == 0
    with open(REPLIES_LOG_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerow(entry)


def decode_mime_words(s):
    if not s:
        return ""
    decoded = ""
    for text, enc in decode_header(s):
        if isinstance(text, bytes):
            decoded += text.decode(enc or "utf-8", errors="ignore")
        else:
            decoded += text
    return decoded


def get_domain(email_addr):
    if "@" not in email_addr:
        return ""
    return email_addr.split("@")[-1].strip().lower()


def clean_reply_body(body):
    """Strip quoted original-message / signature clutter, keeping just the new reply text."""
    markers = [
        r"\nOn .{0,80} wrote:\n",
        r"\n-{2,}\s*Original Message\s*-{2,}",
        r"\nFrom:\s.*\nSent:\s.*\nTo:\s.*\nSubject:",
        r"\n>.*",
    ]
    for pattern in markers:
        match = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
        if match:
            body = body[:match.start()]
            break
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def extract_body(msg):
    """Extract plain text body, preferring text/plain, falling back to stripped text/html."""
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            disposition = str(part.get("Content-Disposition") or "")
            if part.get_content_type() == "text/plain" and "attachment" not in disposition:
                charset = part.get_content_charset() or "utf-8"
                try:
                    body = part.get_payload(decode=True).decode(charset, errors="ignore")
                except Exception:
                    continue
                break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        html = part.get_payload(decode=True).decode(charset, errors="ignore")
                        body = re.sub(r"<[^>]+>", " ", html)
                    except Exception:
                        continue
                    break
    else:
        charset = msg.get_content_charset() or "utf-8"
        try:
            body = msg.get_payload(decode=True).decode(charset, errors="ignore")
        except Exception:
            body = str(msg.get_payload())

    return clean_reply_body(body)


def is_auto_reply(subject, from_addr):
    subj_lower = (subject or "").lower()
    from_lower = (from_addr or "").lower()
    if any(k in subj_lower for k in AUTO_REPLY_SUBJECT_KEYWORDS):
        return True
    if any(k in from_lower for k in BOUNCE_SENDER_KEYWORDS):
        return True
    return False


def summarize_with_gemini(api_key, reply_text, company_name):
    prompt = (
        f"You are triaging a cold job outreach reply. The text below is from a recruiter/contact at "
        f"{company_name}, responding to a job application email.\n\n"
        f"Reply text:\n\"\"\"\n{reply_text[:3000]}\n\"\"\"\n\n"
        f"Classify the reply and summarize it."
    )
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {
                        "intent": {
                            "type": "STRING",
                            "enum": ["Interested", "Requesting More Info", "Rejection", "Auto-Reply", "Other"],
                        },
                        "summary": {"type": "STRING"},
                    },
                    "required": ["intent", "summary"],
                },
            },
        },
        timeout=30,
    )
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"intent": "Other", "summary": text[:300]}


def detect_bounces(imap, contacted, seen_ids):
    """Marks companies 'Bounced' when Gmail's mailer-daemon reports their address undeliverable."""
    dates = [r["date_sent"][:10] for r in contacted if r.get("date_sent")]
    if not dates:
        return 0
    since = datetime.strptime(min(dates), "%Y-%m-%d").strftime("%d-%b-%Y")
    status, data = imap.search(None, f'(FROM "mailer-daemon" SINCE "{since}")')
    if status != "OK" or not data or not data[0]:
        return 0
    by_email = {r["email"].lower(): r for r in contacted if r.get("email")}
    count = 0
    for num in data[0].split():
        status, msg_data = imap.fetch(num, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        message_id = (msg.get("Message-ID") or "").strip()
        if message_id and message_id in seen_ids:
            continue
        text = (extract_body(msg) or "").lower()
        for addr, row in by_email.items():
            if addr in text and row["status"] != "Bounced":
                row["status"] = "Bounced"
                row["notes"] = (row.get("notes", "") + " Bounced " + now_str()[:10]).strip()
                log_reply({
                    "date_received": msg.get("Date", ""), "company_name": row["company_name"],
                    "from_email": "mailer-daemon", "subject": decode_mime_words(msg.get("Subject", "")),
                    "message_id": message_id, "intent": "Bounced",
                    "summary": f"Email to {addr} could not be delivered.",
                })
                print(f"[Bounce] {row['company_name']} <{addr}> bounced.")
                count += 1
                break
    return count


def check_inbox():
    sender_email = config.get("SENDER_EMAIL")
    password = config.get("GMAIL_APP_PASSWORD")
    api_key = config.get("GOOGLE_AI_STUDIO_API_KEY")

    if not sender_email or not password:
        print("[CheckEmails] Error: SENDER_EMAIL or GMAIL_APP_PASSWORD missing in .env.")
        return
    if not api_key or "your_" in api_key:
        api_key = None
        print("[CheckEmails] No GOOGLE_AI_STUDIO_API_KEY: replies will be logged without AI classification.")

    queue = load_queue()
    if not queue:
        print("[CheckEmails] outreach_queue.csv is empty or missing.")
        return

    contacted = [r for r in queue if r["status"] in ("Sent", "Followup-Sent") and r.get("email")]
    if not contacted:
        print("[CheckEmails] No companies have been emailed yet, nothing to check replies for.")
        return

    # Group contacted companies by domain so we can search the inbox once per domain
    domain_rows = {}
    for r in contacted:
        domain = get_domain(r["email"])
        if domain:
            domain_rows.setdefault(domain, []).append(r)

    seen_ids = load_seen_message_ids()

    print(f"[CheckEmails] Connecting to Gmail inbox for {sender_email}...")
    context = ssl.create_default_context()
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=context)
    new_reply_count = 0

    try:
        imap.login(sender_email, password)
        imap.select("INBOX")

        for domain, rows in domain_rows.items():
            earliest_sent = min(
                (r["date_sent"].split(" | ")[0].split(" ")[0] for r in rows if r.get("date_sent")),
                default=None,
            )
            since_clause = ""
            if earliest_sent:
                try:
                    since_date = datetime.strptime(earliest_sent, "%Y-%m-%d")
                    since_clause = f' SINCE "{since_date.strftime("%d-%b-%Y")}"'
                except ValueError:
                    since_clause = ""

            status, data = imap.search(None, f'(FROM "{domain}"{since_clause})')
            if status != "OK" or not data or not data[0]:
                continue

            for num in data[0].split():
                status, msg_data = imap.fetch(num, "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue

                msg = email.message_from_bytes(msg_data[0][1])
                message_id = (msg.get("Message-ID") or "").strip()
                if message_id and message_id in seen_ids:
                    continue

                from_addr = parseaddr(msg.get("From", ""))[1]
                subject = decode_mime_words(msg.get("Subject", ""))

                if is_auto_reply(subject, from_addr):
                    continue

                body = extract_body(msg)
                if not body:
                    continue

                matched_row = next(
                    (r for r in rows if r["email"].lower() == from_addr.lower()),
                    rows[0],
                )
                company_name = matched_row["company_name"]

                if api_key:
                    try:
                        result = summarize_with_gemini(api_key, body, company_name)
                    except Exception as e:
                        print(f"[CheckEmails] Gemini failed ({e}); logging reply unclassified.")
                        result = {"intent": "Other", "summary": " ".join(body.split())[:280]}
                else:
                    result = {"intent": "Other", "summary": " ".join(body.split())[:280]}

                log_reply({
                    "date_received": msg.get("Date", ""),
                    "company_name": company_name,
                    "from_email": from_addr,
                    "subject": subject,
                    "message_id": message_id,
                    "intent": result.get("intent", "Other"),
                    "summary": result.get("summary", ""),
                })

                matched_row["status"] = "Replied"
                new_reply_count += 1

                print(f"\n[Reply] {company_name} <{from_addr}> — {result.get('intent')}")
                print(f"  {result.get('summary')}")
        new_reply_count += detect_bounces(imap, contacted, seen_ids)
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    save_queue(queue)

    if new_reply_count == 0:
        print("\n[CheckEmails] No new replies found.")
    else:
        print(f"\n[CheckEmails] Found and summarized {new_reply_count} new replies. See {REPLIES_LOG_FILE} for the full log.")


if __name__ == "__main__":
    check_inbox()
