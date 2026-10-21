"""
Gmail SMTP client. Builds the cold email and the in-thread follow-up from profile.json.
"""
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid, formatdate

from config import config
from common import load_profile, track_info


def _plain(html_text):
    return re.sub(r"<[^>]+>", "", html_text).replace("&amp;", "&")


def signature(profile):
    lines = [profile["name"], profile["location"], f"Phone: {profile['phone']}", f"Email: {profile['email']}"]
    for key, label in (("linkedin", "LinkedIn"), ("github", "GitHub"), ("portfolio", "Portfolio")):
        if profile.get(key):
            lines.append(f"{label}: {profile[key]}")
    return "\n".join(lines)


def greeting(recipient_name):
    from cover_letter_generator import salutation_for
    return f"Dear {salutation_for(recipient_name)},"


def outreach_body(profile, row):
    t = track_info(profile, row.get("track"))
    role = row.get("role_title") or t["role_title"]
    bullets = "\n".join(f"- {_plain(h)}" for h in t["highlights"])
    return (
        f"{greeting(row.get('recipient_name'))}\n\n"
        f"I hope you are well. My name is {profile['name']}, and I am {profile['background']}.\n\n"
        f"I am reaching out to express my interest in {role} opportunities at {row['company_name']}. "
        f"I have attached my CV and a short cover letter tailored to your team.\n\n"
        f"A few highlights:\n{bullets}\n\n"
        f"If there is a current or upcoming opening, I would really appreciate a short conversation "
        f"about how I could contribute at {row['company_name']}.\n\n"
        f"Thank you for your time.\n\n"
        f"Best regards,\n\n{signature(profile)}\n"
    )


def followup_body(profile, row):
    t = track_info(profile, row.get("track"))
    role = row.get("role_title") or t["role_title"]
    return (
        f"{greeting(row.get('recipient_name'))}\n\n"
        f"I hope you are well. I am following up on the note I sent last week about {role} "
        f"opportunities at {row['company_name']}.\n\n"
        f"I am still very interested in your team's work and would be glad to have a brief 10-minute "
        f"call if that is convenient. I have attached my CV again for easy reference.\n\n"
        f"Thank you for your time and consideration.\n\n"
        f"Best regards,\n\n{signature(profile)}\n"
    )


class EmailSender:
    def __init__(self, profile=None):
        self.profile = profile or load_profile()
        self.sender_email = config.get("SENDER_EMAIL") or self.profile["email"]
        self.password = config.get("GMAIL_APP_PASSWORD")
        if not self.password:
            raise ValueError("GMAIL_APP_PASSWORD is missing in .env (see README, step 2).")
        if self.sender_email.lower() != self.profile["email"].lower():
            print(f"[EmailSender] Note: sending from {self.sender_email}, profile email is {self.profile['email']}.")

    def _attach_pdf(self, msg, path):
        if not os.path.exists(path):
            print(f"[EmailSender] Missing attachment: {path}")
            return False
        with open(path, "rb") as f:
            msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename=os.path.basename(path))
        return True

    def _send(self, msg, to_addr):
        context = ssl.create_default_context()
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as smtp:
                smtp.login(self.sender_email, self.password)
                smtp.send_message(msg)
            print(f"[EmailSender] Sent to {to_addr}")
            return True
        except Exception as e:
            print(f"[EmailSender] SMTP error sending to {to_addr}: {e}")
            return False

    def _base_message(self, to_addr, subject):
        msg = EmailMessage()
        msg["From"] = f"{self.profile['name']} <{self.sender_email}>"
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=self.sender_email.split("@")[-1])
        return msg

    def send_outreach(self, row, cover_letter_path, cv_path):
        """Returns the Message-ID on success (stored so follow-ups thread properly), else None."""
        if not row.get("email"):
            return None
        t = track_info(self.profile, row.get("track"))
        msg = self._base_message(row["email"], t["subject"])
        msg.set_content(outreach_body(self.profile, row))
        if not (self._attach_pdf(msg, cover_letter_path) and self._attach_pdf(msg, cv_path)):
            return None
        return msg["Message-ID"] if self._send(msg, row["email"]) else None

    def send_followup(self, row, cv_path, dry_run=False):
        t = track_info(self.profile, row.get("track"))
        msg = self._base_message(row["email"], f"Re: {t['subject']}")
        if row.get("message_id"):
            msg["In-Reply-To"] = row["message_id"]
            msg["References"] = row["message_id"]
        msg.set_content(followup_body(self.profile, row))
        if not self._attach_pdf(msg, cv_path):
            return False
        if dry_run:
            print(f"[DRY-RUN] Would follow up with {row['company_name']} <{row['email']}>")
            return True
        return self._send(msg, row["email"])
