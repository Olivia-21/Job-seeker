"""
Builds a one-page PDF cover letter from profile.json. The middle section
(pitch + three highlights) comes from the role track the company was found under.
"""
from datetime import datetime
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer
from xml.sax.saxutils import escape

from common import load_profile, track_info

rl_config.invariant = 1  # same input -> byte-identical PDF (keeps git history clean)

GENERIC_RECIPIENT_WORDS = [
    "team", "manager", "recruiting", "talent", "acquisition", "hr", "resources",
    "hiring", "services", "solutions", "technology", "tech", "department",
]


def salutation_for(recipient_name):
    name = (recipient_name or "").strip()
    if not name or any(k in name.lower() for k in GENERIC_RECIPIENT_WORDS):
        return "Hiring Team"
    words = name.split()
    if words[0].lower().rstrip(".") in {"mr", "ms", "mrs", "dr", "prof", "eng", "ing"} and len(words) >= 2:
        return f"{words[0]} {words[-1]}"
    return words[0]


class CoverLetterGenerator:
    def __init__(self, profile=None):
        self.profile = profile or load_profile()
        self.primary = colors.HexColor("#1F3A5F")   # deep navy
        self.accent = colors.HexColor("#2A78D6")    # blue rule
        self.ink = colors.HexColor("#1a1a1a")
        self.body_ink = colors.HexColor("#2b2b2b")
        self.muted = colors.HexColor("#555555")

    def _styles(self):
        def S(name, font, size, **kw):
            return ParagraphStyle(name, fontName=font, fontSize=size, leading=size * 1.45, **kw)
        return {
            "name": S("N", "Helvetica-Bold", 17, textColor=self.ink, spaceAfter=1),
            "sub": S("Sub", "Helvetica-Bold", 10, textColor=self.primary, spaceAfter=2),
            "contact": S("C", "Helvetica", 8.5, textColor=self.muted, spaceAfter=2),
            "meta": S("M", "Helvetica", 9.5, textColor=self.muted, spaceAfter=2),
            "body": S("B", "Helvetica", 10, textColor=self.body_ink, alignment=TA_JUSTIFY, spaceAfter=7),
            "bullet": S("Bl", "Helvetica", 10, textColor=self.body_ink, alignment=TA_JUSTIFY,
                        spaceAfter=6, leftIndent=12, bulletIndent=0),
            "sign": S("Sg", "Helvetica", 10, textColor=self.body_ink, spaceAfter=0),
        }

    def contact_line(self):
        p = self.profile
        link_color = self.primary.hexval()
        bits = [escape(p["phone"]), escape(p["email"])]
        for key in ("linkedin", "github", "portfolio"):
            url = (p.get(key) or "").strip()
            if url:
                href = url if url.startswith("http") else f"https://{url}"
                label = url.replace("https://", "").replace("http://", "").rstrip("/")
                bits.append(f'<a href="{escape(href)}" color="{link_color}">{escape(label)}</a>')
        bits.append(escape(p["location"]))
        return "&#160;&#160;|&#160;&#160;".join(bits)

    def body_paragraphs(self, company_name, track_key, role_title=None):
        p = self.profile
        t = track_info(p, track_key)
        role = role_title or t["role_title"]
        company = escape(company_name)
        intro = (
            f"I am writing to express my interest in {role} opportunities at {company}. "
            f"I am {escape(p['background'])}. I would welcome the chance to bring that mix of "
            f"engineering, data and hands-on problem solving to your team."
        )
        closing = (
            f"Thank you for your time and consideration. I have attached my CV and would be glad to "
            f"discuss how my {escape(t['closing_focus'])} could support the work at {company}."
        )
        return intro, escape(t["pitch"]), t["highlights"], closing

    def generate_pdf(self, company_name, track_key, recipient_name, company_address, output_path, role_title=None):
        p = self.profile
        t = track_info(p, track_key)
        st = self._styles()
        doc = SimpleDocTemplate(
            output_path, pagesize=A4,
            leftMargin=0.85 * inch, rightMargin=0.85 * inch,
            topMargin=0.6 * inch, bottomMargin=0.6 * inch,
            title=f"Cover Letter - {company_name}", author=p["name"],
        )
        story = [
            Paragraph(escape(p["name"]), st["name"]),
            Paragraph(escape(role_title or t["role_title"]), st["sub"]),
            Paragraph(self.contact_line(), st["contact"]),
            HRFlowable(width="100%", thickness=1.5, color=self.accent, spaceAfter=8, spaceBefore=4),
            Paragraph(datetime.now().strftime("%d %B %Y"), st["meta"]),
            Spacer(1, 4),
            Paragraph(escape(recipient_name or "Hiring Team"), st["meta"]),
            Paragraph(escape(company_name), st["meta"]),
        ]
        if company_address:
            story.append(Paragraph(escape(company_address), st["meta"]))
        story.append(Spacer(1, 8))
        story.append(Paragraph(f"Dear {escape(salutation_for(recipient_name))},", st["body"]))

        intro, pitch, highlights, closing = self.body_paragraphs(company_name, track_key, role_title)
        story.append(Paragraph(intro, st["body"]))
        story.append(Paragraph(pitch, st["body"]))
        for h in highlights:  # highlights may contain <b> markup by design
            story.append(Paragraph(h, st["bullet"], bulletText="•"))
        story.append(Paragraph(closing, st["body"]))

        story += [
            Spacer(1, 6),
            Paragraph("Sincerely,", st["sign"]),
            Spacer(1, 4),
            Paragraph(f"<b>{escape(p['name'])}</b>", st["sign"]),
            Paragraph(escape(p["headline"]), st["contact"]),
        ]
        doc.build(story)
        print(f"[Generator] Cover letter -> {output_path}")
        return output_path
