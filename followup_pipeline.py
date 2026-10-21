"""
Sends one polite in-thread follow-up to companies emailed N+ days ago with no reply.
  python followup_pipeline.py        -> dry run (shows who would get one)
  python followup_pipeline.py run    -> sends for real
"""
import random
import sys
import time
from datetime import datetime, timedelta

from common import load_profile, load_queue, save_queue, append_log, cv_path, now_str

PROFILE = load_profile()
LIMITS = PROFILE.get("limits", {})


def candidates(queue):
    days = int(LIMITS.get("followup_after_days", 6))
    cutoff = datetime.now() - timedelta(days=days)
    out = []
    for r in queue:
        if r["status"] != "Sent" or not r.get("date_sent") or r.get("date_followup"):
            continue
        try:
            sent = datetime.strptime(r["date_sent"][:10], "%Y-%m-%d")
        except ValueError:
            continue
        if sent <= cutoff:
            out.append(r)
    return out


def run_followups(dry_run=True):
    queue = load_queue()
    cands = candidates(queue)
    cap = int(LIMITS.get("daily_followup_cap", 10))
    print(f"[Follow-up] {len(cands)} eligible (sent {LIMITS.get('followup_after_days', 6)}+ days ago, no reply).")
    if not cands:
        return
    to_send = cands[:cap]
    from email_sender import EmailSender, followup_body
    if dry_run:
        for r in to_send:
            print(f"\n--- {r['company_name']} <{r['email']}> ---\n{followup_body(PROFILE, r)}")
        print("Dry run only. Run `python followup_pipeline.py run` to send.")
        return

    sender = EmailSender(PROFILE)
    cv = cv_path(PROFILE)
    sent = 0
    for i, r in enumerate(to_send):
        if sender.send_followup(r, cv):
            r["status"], r["date_followup"] = "Followup-Sent", now_str()
            append_log(r, "followup", "SUCCESS")
            sent += 1
        else:
            append_log(r, "followup", "FAILED")
        save_queue(queue)
        if i < len(to_send) - 1:
            delay = random.randint(30, 60)
            print(f"[Follow-up] Waiting {delay}s...")
            time.sleep(delay)
    print(f"[Follow-up] Sent {sent} of {len(to_send)}.")


if __name__ == "__main__":
    run_followups(dry_run=not (len(sys.argv) > 1 and sys.argv[1] == "run"))
