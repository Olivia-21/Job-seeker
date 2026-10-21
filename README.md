# Job Seeker

A personal cold-outreach pipeline with a progress dashboard. It finds companies in **Accra and remote**, finds the right contact, writes a tailored cover letter, emails it with your CV from **your personal Gmail** (`SENDER_EMAIL`), follows up once, and reads the replies.

Adapted from [Bentil4/Job_Seeker](https://github.com/Bentil4/Job_Seeker) by Broderick Bentil. The original's lead data was not copied over, and this repo is meant to stay **private**.

## Roles targeted

| Track | Covers | Priority |
|---|---|---|
| Data / Analytics Engineer | Data Engineer, Analytics Engineer, ETL/BI Developer, Fabric/Azure | Prime |
| Data Analyst / Specialist | Data Analyst, Data Specialist, BI/Reporting Analyst | Prime |
| Data Scientist / ML | Data Scientist, ML Engineer, AI roles | Prime |
| Research | Research Analyst, Research Engineer, Research Scientist, M&E data roles | Prime |
| IT Specialist / Support | IT Specialist, IT Support Officer, Systems Admin, Network/Field Technician | Prime |
| Electrical Engineer | Graduate/maintenance electrical, solar PV | Plus |

Searches rotate through **every** query in turn (4 a day, Accra + Remote), so each role gets searched regularly. Queries are worded to find actual job ads ("vacancy", "job opening"), and for every company the bot saves the **direct job posting link** (an individual job ad is preferred over a homepage). Companies still missing a link get another check every morning. Edit the queries in `profile.json`.

**Stages:** To review → Approved → Sent → Followed up → Replied / Interview / Offer. A company whose website was checked but had no email goes to **Needs Contact**. Add the email you find in the dashboard and it returns to review.

## How it flows

```
DISCOVER            ENRICH                 YOU                  SEND & TRACK
Exa search   ──>    Apify crawls each  ──>  Approve in     ──>  Gmail SMTP (cap 15/day)
(2 queries/day)     site for emails;        dashboard           Follow-up after 6 days
                    Hunter finds a named                        check_emails.py reads replies
                    person (10/run)
```

Nothing is ever sent without you approving it first.

## Setup (Windows, one time)

```powershell
cd $HOME\Desktop\Job-Seeker\livy-job-seeker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # then open .env and fill in the keys
```

What goes in `.env`:

- **EXA_API_KEY**: [exa.ai](https://exa.ai), 1,000 free searches a month.
- **APIFY_API_TOKEN**: [apify.com](https://apify.com), crawls company sites for emails.
- **HUNTER_API_KEY**: optional, [hunter.io](https://hunter.io). Turns info@/careers@ into a named person (data, IT, HR or hiring roles first). It checks at most 10 companies per run (`verify_batch` in `profile.json`) and never checks the same company twice, to save free credits.
- **SENDER_EMAIL**: your personal Gmail address.
- **GMAIL_APP_PASSWORD**: a 16-character App Password for that Gmail account. Turn on 2-Step Verification, then create one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). The same password is used for sending and for reading replies.
- **GOOGLE_AI_STUDIO_API_KEY**: optional, for reply summaries ([aistudio.google.com/apikey](https://aistudio.google.com/apikey)).

Fill in `linkedin`, `github` and `portfolio` in `profile.json` if you want them on your letters and emails.

Put your CV PDF in this folder and set its filename in `cv_file` in `profile.json` (default `Your_Name_CV.pdf`).

## Daily use (all online)

Open your dashboard link on any device. That's it.

1. **Every morning at 06:45** the bot searches for companies, finds contacts, writes cover letters and checks your inbox for replies. You get a digest email when it's done.
2. In **Opportunities**, open each listing, then **Approve** or **Skip**.
3. Click **Send N approved**. GitHub sends them from your Gmail with your CV and a cover letter, up to 15 a day and 30–60 seconds apart.
4. When follow-ups are due, **Quick actions → Send follow-ups** sends one polite in-thread nudge to each company that hasn't replied.
5. When a company replies, set its stage (Interview, Offer, Rejected) from its card.

Quick actions also include: **Check replies**, **Find new companies**, **Find listing links**, **Send a test to myself**, and **Replace CV** (in the "Your CV" card).

You only need your PC (and `git pull`) when changing the code or design. The same commands still work locally if you ever want them:

```powershell
python dashboard.py                         # local dashboard at http://127.0.0.1:8765
python outreach_pipeline.py daily | send | test-send | backfill-sources | refilter | status
python followup_pipeline.py [run]
python check_emails.py
```

## Dashboard

There are two ways to open it. Both run the same app (`web/index.html`):

- **Online (recommended):** your Vercel link, which works on any device and is password protected. It reads and writes the queue in your private GitHub repo, so nothing needs to be running on your PC.
- **On your PC:** `python dashboard.py` opens http://127.0.0.1:8765. It reads the local CSVs and can preview cover letters.

It has three pages:

- **Overview:** applications sent, how many companies are waiting for review, reply rate, interviews, the pipeline funnel, emails sent per day, a breakdown by role, reply outcomes and the newest opportunities.
- **Opportunities:** one card per company showing the job title, location, contact, skills and stage. **View original listing** opens the page where the opportunity was found. If no link was saved, **Search for listing** runs a web search instead. Click a card to see the details, change the stage or role track, or add notes. You can approve or skip companies one at a time or in bulk.
- **Replies:** inbox replies classified by `check_emails.py`.

Listing links are saved automatically for new companies. To fill them in for older companies, run `python outreach_pipeline.py backfill-sources` (one Exa search per company).

## Hosted dashboard (Vercel), one-time setup

1. **Create a GitHub token** the dashboard can use to read and write your repo. Go to github.com, then **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**:
   - Repository access: **Only select repositories → job-seeker**
   - Permissions: **Contents: Read and write**, **Actions: Read and write** (lets the dashboard start a search and show the bot's status)
   - Expiration: up to a year. Copy the token.
2. **Import the repo on Vercel.** Sign in at vercel.com with GitHub, then **Add New → Project → Import `job-seeker`**:
   - **Root Directory: `web`** (important)
   - Framework preset: **Other**. Leave the build settings empty.
   - Add these **Environment Variables**:
     | Name | Value |
     |---|---|
     | `GITHUB_REPO` | `your-github-username/job-seeker` |
     | `GITHUB_TOKEN` | the token from step 1 |
     | `DASHBOARD_PASSWORD` | a password you choose for the dashboard |
     | `SESSION_SECRET` | any long random string (run `python -c "import secrets;print(secrets.token_urlsafe(32))"`) |
   - Click **Deploy**. Vercel gives you a link like `https://job-seeker.vercel.app`.
3. Open the link, enter your password and bookmark it. On a phone, choose **Add to Home Screen**.

Vercel only redeploys when files in `web/` change. The bot's daily commits don't trigger a deploy, and they don't need to, because the dashboard always reads live data from GitHub.

**Where things live:** everything (queue, logs, cover letters, your CV) is in your private GitHub repo. The dashboard and the bot both edit it, and `merge_queue.py` merges their changes row by row, so an approval made while the bot is running is never lost. Bot jobs queue up and never run at the same time.

## Lead filters

`profile.json → lead_filters` stops bad leads before they reach the queue:

- government and education domains (`.gov.gh`, `.edu.gh` and so on)
- job boards and aggregators (LinkedIn, Indeed, Jobberman and others)
- place names posing as companies ("Accra", "Ghana")

Add to these lists as you spot junk.

## Private repo

Run once from the VS Code terminal in this folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_private_repo.ps1
```

It commits under the name and email set in the script, signs you in to GitHub if needed, and creates a **private** repo called `job-seeker`. After that, use VS Code's Source Control panel to commit and push updates.

`.env`, your CV and the generated letters are git-ignored, so they never leave your PC.

## GitHub Actions

- **Daily Outreach Prep** (`daily-outreach-prep.yml`): every day at 06:45 Accra time. It runs search → scrape → Hunter → letters → check replies, then sends a digest email. It never sends applications.
- **Job Seeker Actions** (`job-seeker-actions.yml`): started by the dashboard buttons. It handles send, follow-ups, check replies, letters, listing links, re-filter and test send.

Repo secrets (**Settings → Secrets and variables → Actions**): `EXA_API_KEY`, `APIFY_API_TOKEN`, `HUNTER_API_KEY`, `SENDER_EMAIL`, `GMAIL_APP_PASSWORD`, and optionally `GOOGLE_AI_STUDIO_API_KEY` for AI reply summaries. Without that key, replies are still logged, just without a summary. `gh secret set -f .env` uploads them all at once.

## Safeguards

- Sending only happens when you click Send (and confirm), and only to companies you approved.
- At most 15 first-contact emails and 10 follow-ups a day, with a random 30–60 second gap between them.
- One follow-up per company, sent in the same email thread.
- Replies, bounces and status changes stop any further follow-ups to that company.
