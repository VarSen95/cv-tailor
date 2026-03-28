#!/usr/bin/env python3
"""
cv_tailor_daily.py
==================
Scans Gmail for LinkedIn job alerts, tailors your CV for each role
using Claude, saves each as a Google Doc, and emails you a summary.

SETUP (one time)
────────────────
1. Install dependencies:
   pip install anthropic google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client

2. Create Google Cloud credentials:
   - Go to https://console.cloud.google.com
   - Create a new project (or use existing)
   - Enable: Gmail API, Google Drive API, Google Docs API
   - Go to APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID
   - Application type: Desktop App
   - Download the JSON → save as credentials.json in the same folder as this script

3. Set your Anthropic API key:
   export ANTHROPIC_API_KEY=sk-ant-...

4. Run once to authorise Google (opens browser):
   python3 cv_tailor_daily.py

   After authorising, token.json is saved — no browser needed on future runs.

SCHEDULE (cron, runs daily at 08:00 UTC)
────────────────────────────────────────
   0 8 * * * cd /path/to/script && ANTHROPIC_API_KEY=sk-ant-... python3 cv_tailor_daily.py

GITHUB ACTIONS
──────────────
   See cv_tailor.yml — add ANTHROPIC_API_KEY + GOOGLE_CREDENTIALS_JSON as secrets.
"""

import concurrent.futures
import anthropic
import base64
import json
import os
import re
import sys
import threading
from datetime import date, datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Config ────────────────────────────────────────────────────────────────────

YOUR_EMAIL    = "varshasengupta95@gmail.com"
TARGET_CITIES = ["Amsterdam", "Dublin", "London"]
DRIVE_FOLDER  = "Tailored CVs"
MODEL         = "claude-haiku-4-5-20251001"
LOOKBACK_HRS  = 25
MAX_JOBS     = 10   # cap per run — change as needed
# Email sending is ON by default. Set CV_TAILOR_SEND_EMAIL=false (or 0/no/off)
# to temporarily pause summary emails without changing code.
EMAIL_SENDING_ENABLED = os.environ.get("CV_TAILOR_SEND_EMAIL", "true").lower() not in (
    "0", "false", "no", "off"
)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

BASE_DIR = Path(__file__).parent

# Lock to protect shared Drive/Docs client usage across threads
drive_lock = threading.Lock()

BASE_CV = r"""
\documentclass[letterpaper,11pt]{article}
\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage{marvosym}
\usepackage[usenames,dvipsnames]{color}
\usepackage{verbatim}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage[english]{babel}
\usepackage{tabularx}
\usepackage{fontawesome5}
\usepackage{multicol}
\usepackage{graphicx}
\setlength{\multicolsep}{-3.0pt}
\setlength{\columnsep}{-1pt}
\input{glyphtounicode}
\RequirePackage{xcolor}
\definecolor{airforceblue}{rgb}{0.36, 0.54, 0.66}
\usepackage{CormorantGaramond}
\usepackage{charter}
\addtolength{\oddsidemargin}{-0.6in}
\addtolength{\evensidemargin}{-0.5in}
\addtolength{\textwidth}{1.19in}
\addtolength{\topmargin}{-.7in}
\addtolength{\textheight}{1.4in}
\urlstyle{same}
\raggedbottom
\raggedright
\setlength{\tabcolsep}{0in}
\titleformat{\section}{\vspace{-4pt}\scshape\raggedright\large\bfseries}{}{0em}{}[\titlerule \vspace{-5pt}]
\pdfgentounicode=1
\newcommand{\resumeItem}[1]{\item\small{#1}}
\newcommand{\resumeSubheading}[4]{
  \vspace{-2pt}\item
    \begin{tabular*}{1.0\textwidth}{l@{\extracolsep{\fill}}r}
      {\large #1} & {\small #2} \\
      \textit{\large #3} & \textit{\small #4} \\
    \end{tabular*}\vspace{-2pt}
}
\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0in, label={}, itemsep=10pt]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}[leftmargin=0.12in]}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-6pt}}

\begin{document}

%----------HEADING----------
\begin{center}
    {\huge Varsha Sengupta} \\ \vspace{2pt}
    Dublin, Ireland \\
    +353 89 4141802 \;|\;
    \href{mailto:varshasengupta95@gmail.com}{varshasengupta95@gmail.com} \;|\;
    \href{https://www.linkedin.com/in/varsha-sengupta/}{linkedin.com/in/varsha-sengupta} \;|\;
    \href{https://github.com/VarSen95}{github.com/VarSen95}
\end{center}

%-----------SUMMARY-----------
\section{\color{airforceblue}SUMMARY}
\begin{itemize}[leftmargin=0.12in]
\small{Senior Software Engineer with 6+ years of experience building scalable, cloud-native systems
and full-stack applications. Proven track record of designing and delivering end-to-end platforms,
re-architecting legacy systems into event-driven architectures, and integrating AI capabilities into
production workflows.}
\end{itemize}
\vspace{-10pt}

%-----------TECHNICAL SKILLS-----------
\section{\color{airforceblue}TECHNICAL SKILLS}
\begin{itemize}[leftmargin=0in, label={}]
\small{\item{
\textbf{Languages:} Java, Kotlin, Python, JavaScript, TypeScript \\
\textbf{Frontend:} React, React Native, Micro-frontend architecture, SPA design, State management \\
\textbf{Backend \& Data:} RESTful APIs, Event-driven systems, PostgreSQL (Aurora), MySQL, DynamoDB \\
\textbf{Cloud \& Infrastructure:} AWS (Lambda, SQS, SNS, EventBridge, S3), CI/CD, Docker, Kubernetes \\
\textbf{Engineering Practices:} Unit \& integration testing, Feature flag rollouts, Monitoring \& alerting
}}
\end{itemize}
\vspace{-14pt}

%-----------WORK EXPERIENCE-----------
\section{\color{airforceblue}WORK EXPERIENCE}
\resumeSubHeadingListStart
\resumeSubheading
  {Software Engineer}{Sep 2019 -- Present}
  {Amazon}{Dublin, Ireland}
\resumeItemListStart
\resumeItem{Re-architected a legacy backend system into a scalable AWS-native, event-driven architecture,
reducing processing time from 24 hours to ~1 hour (24x improvement) and improving fault isolation.}
\resumeItem{Designed and operated low-latency APIs (p95 < 300 ms) using caching and asynchronous
orchestration, improving throughput and reliability.}
\resumeItem{Architected and deployed Retrieval-Augmented Generation (RAG) pipelines using Amazon
OpenSearch and Bedrock, enabling low-latency AI-powered insights in production systems.}
\resumeItem{Designed and optimized PostgreSQL (Aurora) and DynamoDB data models, improving query
performance through indexing strategies and optimized access patterns.}
\resumeItem{Rebuilt the frontend layer as an independent React-based micro-frontend with isolated
routing and deployment artifacts, enabling domain-aligned ownership and reduced cross-team
release dependencies.}
\resumeItem{Designed and implemented a full-stack internal transfer workflow platform enabling
employees to request and manage internal role transitions through a guided web experience.}
\resumeItem{Developed frontend components supporting real-time AI-driven interactions, improving
responsiveness and user experience in conversational workflows.}
\resumeItem{Strengthened observability through structured logging, metrics instrumentation, and
production alerting, sustaining 99.9\%+ availability.}
\resumeItem{Collaborated with cross-functional teams including product managers and backend engineers
to define requirements and deliver end-to-end solutions.}
\resumeItem{Drove system design and architectural decisions, improving development velocity and
reducing cross-team dependencies.}
\resumeItemListEnd

\resumeSubheading
  {Software Engineering Intern}{Jun 2017 -- Jul 2017}
  {Tata Consultancy Services (TCS)}{Bangalore, India}
\resumeItemListStart
  \resumeItem{Contributed to low-level networking structures using DPDK, focusing on performance
  benchmarking and runtime optimization for high-throughput packet processing.}
\resumeItemListEnd
\resumeSubHeadingListEnd
\vspace{-10pt}

%-----------EDUCATION-----------
\section{\color{airforceblue}EDUCATION}
\resumeSubHeadingListStart
\resumeSubheading
  {University of Limerick}{Limerick, Ireland}
  {Master of Science in Software Engineering}{Sep 2018 -- Aug 2019}
\resumeItemListStart
  \resumeItem{QCA: 3.34 / 4.0}
  \resumeItem{Relevant coursework: Software Design, Software Evolution, Software Quality,
  Parallelism and Concurrency, Philosophy of Research}
\resumeItemListEnd
\resumeSubheading
  {National Institute of Technology (NIT) Durgapur}{Durgapur, India}
  {Bachelor of Technology in Computer Science and Engineering}{Jul 2014 -- Jun 2018}
\resumeItemListStart
  \resumeItem{Relevant coursework: Data Structures and Algorithms, RDBMS, OOP,
  Microprocessors, Artificial Intelligence}
\resumeItemListEnd
\resumeSubHeadingListEnd
\vspace{-8pt}
\end{document}
"""

# ── Google Auth ───────────────────────────────────────────────────────────────

def get_google_creds() -> Credentials:
    """Load or refresh Google OAuth credentials. Opens browser on first run."""
    token_path = BASE_DIR / "token.json"
    creds_path = BASE_DIR / "credentials.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                print("ERROR: credentials.json not found.")
                print("Download it from Google Cloud Console → APIs & Services → Credentials")
                print(f"Save it to: {creds_path}")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return creds


# ── Step 1: Scan Gmail ────────────────────────────────────────────────────────

def fetch_linkedin_jobs(gmail) -> list[dict]:
    """Read LinkedIn alert emails from last LOOKBACK_HRS hours, extract jobs."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HRS)
    after_date = cutoff.strftime("%Y/%m/%d")

    query = (
        f"from:jobalerts-noreply@linkedin.com OR from:jobs-noreply@linkedin.com "
        f"after:{after_date}"
    )

    result = gmail.users().messages().list(userId="me", q=query, maxResults=20).execute()
    messages = result.get("messages", [])
    print(f"  Found {len(messages)} LinkedIn alert email(s)")

    all_jobs = []
    city_lower = [c.lower() for c in TARGET_CITIES]

    for msg_meta in messages:
        msg = gmail.users().messages().get(
            userId="me", id=msg_meta["id"], format="full"
        ).execute()

        # Decode body
        body = ""
        payload = msg.get("payload", {})
        if "body" in payload and payload["body"].get("data"):
            body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
        elif "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                    body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")
                    break

        if not body:
            continue

        # Parse jobs: LinkedIn email format is "Title\nCompany\nCity\n"
        # separated by dashes
        blocks = re.split(r"-{10,}", body)
        for block in blocks:
            lines = [l.strip() for l in block.strip().splitlines() if l.strip()]
            # Filter out non-job lines (URLs, "View job:", "Fast growing", etc.)
            clean = [
                l for l in lines
                if not l.startswith("http")
                and not l.startswith("View job")
                and not l.startswith("Your job")
                and not l.startswith("New jobs")
                and not l.startswith("See all")
                and not l.startswith("This company")
                and not l.startswith("Apply")
                and "alumni" not in l.lower()
                and "connection" not in l.lower()
                and len(l) < 120
            ]
            if len(clean) >= 3:
                title   = clean[0]
                company = clean[1]
                city    = clean[2]
                # Only keep jobs in target cities
                if any(c in city.lower() for c in city_lower):
                    # Skip entry-level
                    title_lower = title.lower()
                    if any(x in title_lower for x in ["entry level", "junior", " i ", "intern", "graduate"]):
                        all_jobs.append({"title": title, "company": company, "city": city, "skip": "entry-level"})
                    else:
                        all_jobs.append({"title": title, "company": company, "city": city, "skip": None})

    # Deduplicate
    seen = set()
    unique = []
    for j in all_jobs:
        key = f"{j['title']}|{j['company']}"
        if key not in seen:
            seen.add(key)
            unique.append(j)

    return unique


# ── Step 2: Tailor CV with Claude ─────────────────────────────────────────────

def tailor_cv(claude: anthropic.Anthropic, job: dict) -> str:
    """Ask Claude to rewrite experience bullets for a specific job."""
    prompt = f"""You are a CV tailoring assistant. The candidate is a Senior Software Engineer at Amazon with 6+ years experience.

Job details:
- Title: {job['title']}
- Company: {job['company']}
- City: {job['city']}

Rewrite ONLY the \\resumeItem{{}} bullets inside the \\resumeItemListStart...\\resumeItemListEnd block for the Amazon role.
Rules:
- All facts must remain 100% truthful — only reframe emphasis and ordering
- Use keywords relevant to {job['company']} and the {job['title']} role naturally
- Keep the exact same number of bullets, each on one line
- Change absolutely nothing else in the CV

Base CV:
{BASE_CV}

Return the complete updated LaTeX file as plain text only. No explanation, no markdown fences."""

    response = claude.messages.create(
        model=MODEL,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


# ── Step 3: Save to Google Drive ──────────────────────────────────────────────

def get_or_create_folder(drive, name: str, parent_id: str = None) -> str:
    """Find or create a Drive folder, return its ID."""
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    results = drive.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    # Create it
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        meta["parents"] = [parent_id]

    folder = drive.files().create(body=meta, fields="id").execute()
    return folder["id"]


def save_to_drive(drive, docs, job: dict, content: str, today: str) -> str:
    """Create a Google Doc with the tailored CV, return its webViewLink."""
    # Get or create Tailored CVs / YYYY-MM-DD folder
    root_id = get_or_create_folder(drive, DRIVE_FOLDER)
    day_id  = get_or_create_folder(drive, today, parent_id=root_id)

    # Create the Google Doc
    safe_title = re.sub(r"[^\w\s-]", "", f"{job['company']} {job['title']}")[:60]
    doc_name = f"{safe_title} {today}"

    doc = docs.documents().create(body={"title": doc_name}).execute()
    doc_id = doc["documentId"]

    # Move to the day folder
    drive.files().update(
        fileId=doc_id,
        addParents=day_id,
        removeParents="root",
        fields="id, parents",
    ).execute()

    # Insert the LaTeX content
    docs.documents().batchUpdate(
        documentId=doc_id,
        body={
            "requests": [{
                "insertText": {
                    "location": {"index": 1},
                    "text": content,
                }
            }]
        },
    ).execute()

    return f"https://docs.google.com/document/d/{doc_id}/edit"


# ── Step 4: Send summary email ────────────────────────────────────────────────

def send_summary_email(gmail, results: list[dict], today: str, folder_link: str):
    """Build the formatted summary email and send it."""

    def city_style(city: str) -> tuple[str, str]:
        c = city.lower()
        if "amsterdam" in c: return "#f3e8ff", "#7c3aed"
        if "london"    in c: return "#dbeafe", "#1d4ed8"
        if "dublin"    in c: return "#dcfce7", "#15803d"
        return "#f3f4f6", "#6b7280"

    tailored = [r for r in results if r["status"] == "tailored"]
    skipped  = [r for r in results if r["status"] == "skipped"]
    errors   = [r for r in results if r["status"] == "error"]

    rows_html = ""
    for i, r in enumerate(results):
        bg = "#fafafa" if i % 2 == 0 else "#ffffff"
        city_bg, city_color = city_style(r["city"])

        if r["status"] == "tailored":
            status_cell = '<span style="background:#dcfce7;color:#15803d;font-size:11px;font-weight:500;padding:2px 8px;border-radius:100px;">✓ saved</span>'
            link_cell   = f'<a href="{r["drive_link"]}" style="color:#2563eb;font-size:12px;text-decoration:none;font-weight:500;">Open ↗</a>'
            title_color = "#111827"
            co_color    = "#374151"
        else:
            reason = r.get("skip_reason") or r.get("error_msg") or r["status"]
            status_cell = f'<span style="background:#f3f4f6;color:#9ca3af;font-size:11px;padding:2px 8px;border-radius:100px;">{reason}</span>'
            link_cell   = '<span style="color:#d1d5db;">—</span>'
            title_color = "#9ca3af"
            co_color    = "#9ca3af"

        rows_html += f"""
        <tr style="border-top:1px solid #e4e4e7;background:{bg};">
          <td style="padding:13px 14px;font-size:13px;color:{title_color};font-weight:500;">{r['title']}</td>
          <td style="padding:13px 14px;font-size:13px;color:{co_color};">{r['company']}</td>
          <td style="padding:13px 14px;"><span style="background:{city_bg};color:{city_color};font-size:11px;font-weight:500;padding:2px 8px;border-radius:100px;">{r['city']}</span></td>
          <td style="padding:13px 14px;">{status_cell}</td>
          <td style="padding:13px 14px;">{link_cell}</td>
        </tr>"""

    status_badge = (
        '<div style="background:#16a34a;color:#fff;font-size:12px;font-weight:600;padding:6px 14px;border-radius:100px;">✓ All clear</div>'
        if not errors else
        '<div style="background:#dc2626;color:#fff;font-size:12px;font-weight:600;padding:6px 14px;border-radius:100px;">⚠ Errors</div>'
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:32px 0;">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e4e4e7;">

  <tr><td style="background:#0f0f11;padding:28px 32px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td>
        <div style="font-family:'Courier New',monospace;font-size:11px;color:#6b7280;letter-spacing:0.1em;margin-bottom:6px;">CV / TAILOR</div>
        <div style="font-size:22px;font-weight:600;color:#fff;margin-bottom:4px;">Daily Report</div>
        <div style="font-size:13px;color:#9ca3af;">{datetime.now().strftime('%A, %d %B %Y')}</div>
      </td>
      <td align="right" valign="middle">{status_badge}</td>
    </tr></table>
  </td></tr>

  <tr><td style="border-bottom:1px solid #e4e4e7;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="25%" align="center" style="padding:20px 0;border-right:1px solid #e4e4e7;">
        <div style="font-size:28px;font-weight:700;color:#0f0f11;">{len(results)}</div>
        <div style="font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:0.08em;margin-top:2px;">jobs found</div>
      </td>
      <td width="25%" align="center" style="padding:20px 0;border-right:1px solid #e4e4e7;">
        <div style="font-size:28px;font-weight:700;color:#16a34a;">{len(tailored)}</div>
        <div style="font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:0.08em;margin-top:2px;">CVs tailored</div>
      </td>
      <td width="25%" align="center" style="padding:20px 0;border-right:1px solid #e4e4e7;">
        <div style="font-size:28px;font-weight:700;color:#6b7280;">{len(skipped)}</div>
        <div style="font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:0.08em;margin-top:2px;">skipped</div>
      </td>
      <td width="25%" align="center" style="padding:20px 0;">
        <div style="font-size:28px;font-weight:700;color:{'#dc2626' if errors else '#6b7280'};">{len(errors)}</div>
        <div style="font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:0.08em;margin-top:2px;">errors</div>
      </td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:28px 32px 8px;">
    <div style="font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:16px;">Tailored CVs — saved to Google Drive</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e4e4e7;border-radius:8px;overflow:hidden;">
      <tr style="background:#f9f9fa;">
        <td style="padding:10px 14px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.08em;width:34%;">Role</td>
        <td style="padding:10px 14px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.08em;width:20%;">Company</td>
        <td style="padding:10px 14px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.08em;width:16%;">City</td>
        <td style="padding:10px 14px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.08em;width:18%;">Status</td>
        <td style="padding:10px 14px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.08em;width:12%;">Doc</td>
      </tr>
      {rows_html}
    </table>
  </td></tr>

  <tr><td style="padding:20px 32px 28px;">
    <table cellpadding="0" cellspacing="0"><tr>
      <td style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:14px 18px;">
        <table cellpadding="0" cellspacing="0"><tr>
          <td style="font-size:20px;padding-right:12px;">📁</td>
          <td>
            <div style="font-size:12px;font-weight:600;color:#15803d;margin-bottom:2px;">All CVs saved to Google Drive</div>
            <a href="{folder_link}" style="font-size:12px;color:#2563eb;text-decoration:none;">Tailored CVs → {today} ↗</a>
          </td>
        </tr></table>
      </td>
    </tr></table>
  </td></tr>

  <tr><td style="background:#f9f9fa;border-top:1px solid #e4e4e7;padding:18px 32px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-size:11px;color:#9ca3af;">Generated by <span style="font-family:'Courier New',monospace;">cv_tailor_daily.py</span> · Next run tomorrow at 08:00 UTC</td>
      <td align="right" style="font-size:11px;color:#9ca3af;">{YOUR_EMAIL}</td>
    </tr></table>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"CV Tailor — {today} — {len(tailored)} job{'s' if len(tailored) != 1 else ''} tailored ✓"
    msg["To"]      = YOUR_EMAIL
    msg.attach(MIMEText(html, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
    print(f"  ✉  Summary email sent to {YOUR_EMAIL}")


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        print("Run: export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        sys.exit(1)

    today = date.today().isoformat()
    print(f"[{today}] CV Tailor starting...")
    print(f"  Cities : {', '.join(TARGET_CITIES)}")
    print(f"  Drive  : {DRIVE_FOLDER}/{today}")
    print()

    # Google auth
    print("  Authenticating with Google...")
    creds = get_google_creds()
    gmail = build("gmail", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    docs  = build("docs",  "v1", credentials=creds)
    claude = anthropic.Anthropic(api_key=api_key)
    print("  ✓ Authenticated\n")

    # Step 1: scan Gmail
    print("Step 1 — Scanning Gmail for LinkedIn alerts...")
    jobs = fetch_linkedin_jobs(gmail)
    jobs = jobs[:MAX_JOBS]  # cap it
    print(f"  ✓ {len(jobs)} job(s) found across target cities\n")

    if not jobs:
        print("  No jobs found in last 25h — nothing to do.")
        return

    # Step 2+3: tailor + save (parallel with up to 5 threads)
    folder_link_ref = {"value": f"https://drive.google.com/drive/search?q={DRIVE_FOLDER}"}
    results: list[dict] = [None] * len(jobs)

    def process_job(index: int, job: dict) -> dict:
        label = f"{job['company']} — {job['title']} ({job['city']})"

        if job.get("skip"):
            print(f"  [{index}/{len(jobs)}] − {label}  [skipped: {job['skip']}]")
            return {**job, "__order": index, "status": "skipped", "skip_reason": job["skip"], "drive_link": None}

        print(f"  [{index}/{len(jobs)}] Tailoring: {label}")
        try:
            tailored_tex = tailor_cv(claude, job)
            with drive_lock:
                drive_link = save_to_drive(drive, docs, job, tailored_tex, today)
                folder_link_ref["value"] = "https://drive.google.com/drive/folders/" + drive_link.split("/d/")[1].split("/")[0]
            print(f"       ✓ Saved → {drive_link}")
            return {**job, "__order": index, "status": "tailored", "drive_link": drive_link}
        except Exception as e:
            print(f"       ✗ Error: {e}")
            return {**job, "__order": index, "status": "error", "error_msg": str(e), "drive_link": None}

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(process_job, i, job) for i, job in enumerate(jobs, 1)]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            order = result.pop("__order")
            results[order - 1] = result

    folder_link = folder_link_ref["value"]

    # Step 4: send email
    if EMAIL_SENDING_ENABLED:
        print(f"\nStep 4 — Sending summary email...")
        try:
            send_summary_email(gmail, results, today, folder_link)
        except Exception as e:
            print(f"  ✗ Email failed: {e}")
    else:
        print("\nStep 4 — Email sending disabled (set CV_TAILOR_SEND_EMAIL=true to enable). Skipping.")

    # Final summary
    tailored = sum(1 for r in results if r["status"] == "tailored")
    errors   = sum(1 for r in results if r["status"] == "error")
    print(f"\n{'─'*50}")
    print(f"  Done — {tailored}/{len(jobs)} CVs tailored")
    print(f"{'─'*50}\n")

    if errors:
        sys.exit(2)


if __name__ == "__main__":
    run()
