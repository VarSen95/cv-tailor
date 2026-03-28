#!/usr/bin/env python3
"""
cv_tailor_daily.py
==================
Scans Gmail for LinkedIn job alerts, scores and ranks jobs by fit,
tailors your CV for the top matches, saves to Google Drive, emails summary.

SETUP (one time)
────────────────
1. pip install anthropic google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
2. Save credentials.json from Google Cloud Console (same folder as this script)
3. export ANTHROPIC_API_KEY=sk-ant-...
4. python3 cv_tailor_daily.py   ← opens browser to authorise Google on first run

CRON (daily at 22:00 UTC)
──────────────────────────
0 22 * * * cd /path/to/script && ANTHROPIC_API_KEY=sk-ant-... python3 cv_tailor_daily.py
"""

import anthropic
import base64
import concurrent.futures
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

YOUR_EMAIL = "varshasengupta95@gmail.com"
TARGET_CITIES = ["Amsterdam", "London", "Dublin"]
DRIVE_FOLDER = "Tailored CVs"
MODEL = "claude-haiku-4-5-20251001"
LOOKBACK_HRS = 25
MAX_JOBS = 20

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
drive_lock = threading.Lock()

# ── Scoring config ────────────────────────────────────────────────────────────

CITY_PRIORITY = {
    "amsterdam": 3,
    "london": 2,
    "dublin": 1,
}

CV_SKILLS = [
    "java", "kotlin", "python", "typescript", "javascript",
    "react", "aws", "lambda", "dynamodb", "postgresql", "aurora",
    "sqs", "sns", "eventbridge", "s3", "kafka",
    "microservice", "micro-frontend", "microfrontend",
    "event-driven", "rest", "api", "docker", "kubernetes", "k8s",
    "ci/cd", "bedrock", "openai", "llm", "rag", "generative ai", "gen ai",
    "full stack", "fullstack", "backend", "cloud", "distributed systems",
    "observability", "monitoring",
]

ROLE_PREFERENCE = [
    "platform engineer",
    "backend engineer",
    "software engineer",
    "senior software engineer",
    "staff engineer",
    "full stack engineer",
    "full-stack engineer",
]

BIG_TECH = [
    "google", "meta", "apple", "amazon", "microsoft", "netflix",
    "uber", "airbnb", "stripe", "openai", "anthropic", "deepmind",
    "spotify", "shopify", "atlassian", "datadog", "snowflake",
    "databricks", "figma", "notion", "linear", "vercel",
    "booking.com", "adyen", "flexport", "revolut", "wise",
]

SKIP_KEYWORDS = [
    "junior", "entry level", "entry-level", "graduate", "intern",
    "qa engineer", "test engineer", "data analyst", "data scientist",
    "machine learning engineer", "ml engineer",
    "ios engineer", "android engineer", "mobile engineer",
    "devops engineer", "sre ", "site reliability",
    "manager", "director", "vp ", "head of",
]

# ── Base CV ───────────────────────────────────────────────────────────────────

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
                print(f"ERROR: credentials.json not found at {creds_path}")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return creds

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_city_priority(city: str) -> int:
    city_l = city.lower()
    for key, value in CITY_PRIORITY.items():
        if key in city_l:
            return value
    return 0

def extract_text_body(payload: dict) -> str:
    if "body" in payload and payload["body"].get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")

    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="ignore")

    return ""

# ── Step 1: Scan Gmail ────────────────────────────────────────────────────────

def fetch_linkedin_jobs(gmail) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HRS)
    after_date = cutoff.strftime("%Y/%m/%d")
    query = (
        f"(from:jobalerts-noreply@linkedin.com OR from:jobs-noreply@linkedin.com) "
        f"after:{after_date}"
    )

    result = gmail.users().messages().list(userId="me", q=query, maxResults=50).execute()
    messages = result.get("messages", [])
    print(f"  Found {len(messages)} LinkedIn alert email(s)")

    raw_jobs = []
    city_lower = [c.lower() for c in TARGET_CITIES]

    for msg_meta in messages:
        msg = gmail.users().messages().get(userId="me", id=msg_meta["id"], format="full").execute()
        payload = msg.get("payload", {})
        body = extract_text_body(payload)

        if not body:
            continue

        blocks = re.split(r"-{10,}", body)
        for block in blocks:
            lines = [l.strip() for l in block.strip().splitlines() if l.strip()]

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

            links = [
                l for l in lines
                if l.startswith("http") and "linkedin.com" in l.lower()
            ]

            if len(clean) >= 3:
                title = clean[0]
                company = clean[1]
                city = clean[2]
                job_link = links[0] if links else None

                if any(c in city.lower() for c in city_lower):
                    raw_jobs.append({
                        "title": title,
                        "company": company,
                        "city": city,
                        "job_link": job_link,
                    })

    seen, unique = set(), []
    for j in raw_jobs:
        key = f"{j['title']}|{j['company']}"
        if key not in seen:
            seen.add(key)
            unique.append(j)

    return unique

# ── Step 2: Score & rank jobs ─────────────────────────────────────────────────

def score_job(job: dict) -> tuple[int, dict]:
    title_l = job["title"].lower()
    company_l = job["company"].lower()
    score = 0
    reasons = []

    for kw in SKIP_KEYWORDS:
        if kw in title_l:
            return -1, {**job, "score": -1, "skip_reason": f"title contains '{kw}'"}

    for i, role in enumerate(ROLE_PREFERENCE):
        if role in title_l:
            pts = 10 + (i * 4)
            score += min(pts, 30)
            reasons.append(f"role:{role}")
            break

    for co in BIG_TECH:
        if co in company_l:
            score += 25
            reasons.append("big-tech")
            break

    skill_hits = []
    for skill in CV_SKILLS:
        if skill in title_l or skill in company_l:
            skill_hits.append(skill)
    score += min(len(skill_hits) * 3, 30)
    if skill_hits:
        reasons.append(f"skills:{','.join(skill_hits[:3])}")

    if any(x in title_l for x in ["senior", "staff", " ii", "ii ", "sr.", "sr "]):
        score += 15
        reasons.append("senior-level")

    city_priority = get_city_priority(job["city"])
    city_bonus = city_priority * 10
    score += city_bonus
    reasons.append(f"city-priority:{job['city']}")

    job["score"] = score
    job["reasons"] = reasons
    job["city_priority"] = city_priority
    return score, job

def filter_and_rank_jobs(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    scored = []
    skipped = []

    for job in jobs:
        score, enriched = score_job(job)
        if score < 0:
            skipped.append({**enriched, "status": "skipped"})
        elif score < 15:
            skipped.append({**enriched, "status": "skipped", "skip_reason": f"low fit score ({score})"})
        else:
            scored.append(enriched)

    scored.sort(
        key=lambda j: (j["score"], j.get("city_priority", 0)),
        reverse=True
    )

    to_tailor = scored[:MAX_JOBS]
    low_score = scored[MAX_JOBS:]

    for j in low_score:
        skipped.append({**j, "status": "skipped", "skip_reason": f"over daily cap (score {j['score']})"})

    return to_tailor, skipped

# ── Step 3: Tailor CV with Claude ─────────────────────────────────────────────

def tailor_cv(claude_client: anthropic.Anthropic, job: dict) -> str:
    prompt = f"""You are a CV tailoring assistant. The candidate is a Senior Software Engineer at Amazon with 6+ years experience.
Her core stack: Java, Kotlin, Python, TypeScript, React, AWS (Lambda/SQS/SNS/EventBridge/S3/Bedrock), DynamoDB, PostgreSQL, micro-frontends, event-driven architecture, RAG pipelines.

Job she is applying for:
- Title: {job['title']}
- Company: {job['company']}
- City: {job['city']}

Rewrite ONLY the \\resumeItem{{}} bullets inside \\resumeItemListStart...\\resumeItemListEnd for the Amazon role.
Rules:
- All facts 100% truthful — only reframe emphasis and ordering
- Front-load bullets most relevant to this specific role and company
- Use keywords from the job title and company naturally
- Keep the exact same number of bullets, each on one line
- Change nothing else in the CV

Base CV:
{BASE_CV}

Return the complete updated LaTeX as plain text only. No explanation, no markdown fences."""

    response = claude_client.messages.create(
        model=MODEL,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()

# ── Step 4: Save to Google Drive ──────────────────────────────────────────────

def get_or_create_folder(drive, name: str, parent_id: str = None) -> str:
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = drive.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        meta["parents"] = [parent_id]
    folder = drive.files().create(body=meta, fields="id").execute()
    return folder["id"]

def save_to_drive(drive, docs, job: dict, content: str, today: str) -> str:
    root_id = get_or_create_folder(drive, DRIVE_FOLDER)
    day_id = get_or_create_folder(drive, today, parent_id=root_id)
    safe_title = re.sub(r"[^\w\s-]", "", f"{job['company']} {job['title']}")[:60]
    doc_name = f"{safe_title} {today}"

    doc = docs.documents().create(body={"title": doc_name}).execute()
    doc_id = doc["documentId"]

    drive.files().update(
        fileId=doc_id, addParents=day_id, removeParents="root", fields="id, parents"
    ).execute()

    docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": [{"insertText": {"location": {"index": 1}, "text": content}}]},
    ).execute()

    return f"https://docs.google.com/document/d/{doc_id}/edit"

# ── Step 5: Send summary email ────────────────────────────────────────────────

def send_summary_email(gmail, tailored: list[dict], skipped: list[dict], today: str, folder_link: str):

    def city_style(city: str) -> tuple[str, str]:
        c = city.lower()
        if "amsterdam" in c:
            return "#f3e8ff", "#7c3aed"
        if "london" in c:
            return "#dbeafe", "#1d4ed8"
        if "dublin" in c:
            return "#dcfce7", "#15803d"
        return "#f3f4f6", "#6b7280"

    def score_bar(score: int) -> str:
        filled = min(round(score / 10), 10)
        bar = "█" * filled + "░" * (10 - filled)
        color = "#16a34a" if score >= 60 else "#f59e0b" if score >= 30 else "#9ca3af"
        return f'<span style="font-family:monospace;font-size:10px;color:{color};">{bar}</span> <span style="font-size:10px;color:#9ca3af;">{score}</span>'

    rows_html = ""
    for i, r in enumerate(tailored):
        bg = "#fafafa" if i % 2 == 0 else "#ffffff"
        city_bg, city_color = city_style(r["city"])

        job_link_html = (
            f'<a href="{r["job_link"]}" style="color:#2563eb;font-size:12px;text-decoration:none;font-weight:500;">Job ↗</a>'
            if r.get("job_link")
            else '<span style="font-size:12px;color:#9ca3af;">—</span>'
        )

        doc_link_html = (
            f'<a href="{r["drive_link"]}" style="color:#2563eb;font-size:12px;text-decoration:none;font-weight:500;">CV ↗</a>'
            if r.get("drive_link")
            else '<span style="font-size:12px;color:#9ca3af;">—</span>'
        )

        rows_html += f"""
        <tr style="border-top:1px solid #e4e4e7;background:{bg};">
          <td style="padding:12px 14px;">
            <div style="font-size:13px;color:#111827;font-weight:500;">{r['title']}</div>
            <div style="font-size:11px;color:#6b7280;margin-top:2px;">{score_bar(r.get('score', 0))}</div>
          </td>
          <td style="padding:12px 14px;font-size:13px;color:#374151;">{r['company']}</td>
          <td style="padding:12px 14px;"><span style="background:{city_bg};color:{city_color};font-size:11px;font-weight:500;padding:2px 8px;border-radius:100px;">{r['city']}</span></td>
          <td style="padding:12px 14px;"><span style="background:#dcfce7;color:#15803d;font-size:11px;font-weight:500;padding:2px 8px;border-radius:100px;">✓ saved</span></td>
          <td style="padding:12px 14px;">{job_link_html}</td>
          <td style="padding:12px 14px;">{doc_link_html}</td>
        </tr>"""

    skipped_rows = ""
    for i, r in enumerate(skipped):
        bg = "#fafafa" if i % 2 == 0 else "#ffffff"
        reason = r.get("skip_reason", "skipped")
        job_link_html = (
            f'<a href="{r["job_link"]}" style="color:#2563eb;font-size:11px;text-decoration:none;font-weight:500;">Job ↗</a>'
            if r.get("job_link")
            else '<span style="font-size:11px;color:#d1d5db;">—</span>'
        )
        skipped_rows += f"""
        <tr style="border-top:1px solid #e4e4e7;background:{bg};">
          <td style="padding:10px 14px;font-size:12px;color:#9ca3af;">{r['title']}</td>
          <td style="padding:10px 14px;font-size:12px;color:#9ca3af;">{r['company']}</td>
          <td style="padding:10px 14px;font-size:11px;color:#d1d5db;">{r['city']}</td>
          <td style="padding:10px 14px;">{job_link_html}</td>
          <td style="padding:10px 14px;font-size:11px;color:#d1d5db;">{reason}</td>
        </tr>"""

    status_badge = (
        '<div style="background:#16a34a;color:#fff;font-size:12px;font-weight:600;padding:6px 14px;border-radius:100px;">✓ All clear</div>'
        if not any(r.get("status") == "error" for r in tailored) else
        '<div style="background:#dc2626;color:#fff;font-size:12px;font-weight:600;padding:6px 14px;border-radius:100px;">⚠ Errors</div>'
    )

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:32px 0;">
<tr><td align="center">
<table width="760" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:12px;overflow:hidden;border:1px solid #e4e4e7;">

  <tr><td style="background:#0f0f11;padding:28px 32px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td>
        <div style="font-family:'Courier New',monospace;font-size:11px;color:#6b7280;letter-spacing:0.1em;margin-bottom:6px;">CV / TAILOR</div>
        <div style="font-size:22px;font-weight:600;color:#fff;margin-bottom:4px;">Daily Report</div>
        <div style="font-size:13px;color:#9ca3af;">{datetime.now().strftime('%A, %d %B %Y')} · Amsterdam · London · Dublin</div>
      </td>
      <td align="right" valign="middle">{status_badge}</td>
    </tr></table>
  </td></tr>

  <tr><td style="border-bottom:1px solid #e4e4e7;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td width="25%" align="center" style="padding:20px 0;border-right:1px solid #e4e4e7;">
        <div style="font-size:28px;font-weight:700;color:#0f0f11;">{len(tailored) + len(skipped)}</div>
        <div style="font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:0.08em;margin-top:2px;">jobs found</div>
      </td>
      <td width="25%" align="center" style="padding:20px 0;border-right:1px solid #e4e4e7;">
        <div style="font-size:28px;font-weight:700;color:#16a34a;">{len(tailored)}</div>
        <div style="font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:0.08em;margin-top:2px;">CVs tailored</div>
      </td>
      <td width="25%" align="center" style="padding:20px 0;border-right:1px solid #e4e4e7;">
        <div style="font-size:28px;font-weight:700;color:#6b7280;">{len(skipped)}</div>
        <div style="font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:0.08em;margin-top:2px;">filtered out</div>
      </td>
      <td width="25%" align="center" style="padding:20px 0;">
        <div style="font-size:28px;font-weight:700;color:#0f0f11;">{MAX_JOBS}</div>
        <div style="font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:0.08em;margin-top:2px;">daily cap</div>
      </td>
    </tr></table>
  </td></tr>

  <tr><td style="padding:28px 32px 8px;">
    <div style="font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px;">Top matches — tailored &amp; saved</div>
    <div style="font-size:11px;color:#9ca3af;margin-bottom:14px;">Ranked by fit score with city priority: Amsterdam &gt; London &gt; Dublin</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e4e4e7;border-radius:8px;overflow:hidden;">
      <tr style="background:#f9f9fa;">
        <td style="padding:10px 14px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;width:28%;">Role + fit</td>
        <td style="padding:10px 14px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;width:18%;">Company</td>
        <td style="padding:10px 14px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;width:14%;">City</td>
        <td style="padding:10px 14px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;width:14%;">Status</td>
        <td style="padding:10px 14px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;width:13%;">Job</td>
        <td style="padding:10px 14px;font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;width:13%;">CV</td>
      </tr>
      {rows_html}
    </table>
  </td></tr>

  {"" if not skipped else f'''
  <tr><td style="padding:16px 32px 8px;">
    <div style="font-size:11px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:12px;">Filtered out</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #f3f4f6;border-radius:8px;overflow:hidden;">
      <tr style="background:#fafafa;">
        <td style="padding:8px 14px;font-size:10px;font-weight:600;color:#9ca3af;text-transform:uppercase;width:32%;">Role</td>
        <td style="padding:8px 14px;font-size:10px;font-weight:600;color:#9ca3af;text-transform:uppercase;width:20%;">Company</td>
        <td style="padding:8px 14px;font-size:10px;font-weight:600;color:#9ca3af;text-transform:uppercase;width:16%;">City</td>
        <td style="padding:8px 14px;font-size:10px;font-weight:600;color:#9ca3af;text-transform:uppercase;width:12%;">Job</td>
        <td style="padding:8px 14px;font-size:10px;font-weight:600;color:#9ca3af;text-transform:uppercase;">Reason</td>
      </tr>
      {skipped_rows}
    </table>
  </td></tr>'''}

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

  <tr><td style="background:#f9f9fa;border-top:1px solid #e4e4e7;padding:16px 32px;">
    <table width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-size:11px;color:#9ca3af;">
        <span style="font-family:'Courier New',monospace;">cv_tailor_daily.py</span> ·
        model: {MODEL} ·
        next run tomorrow 22:00 UTC
      </td>
      <td align="right" style="font-size:11px;color:#9ca3af;">{YOUR_EMAIL}</td>
    </tr></table>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"CV Tailor — {today} — {len(tailored)} top match{'es' if len(tailored) != 1 else ''} ✓"
    msg["To"] = YOUR_EMAIL
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
    print(f"  Cities  : {', '.join(TARGET_CITIES)}")
    print(f"  Model   : {MODEL}")
    print(f"  Cap     : {MAX_JOBS} jobs/run")
    print()

    print("  Authenticating with Google...")
    creds = get_google_creds()
    gmail = build("gmail", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    docs = build("docs", "v1", credentials=creds)
    claude_client = anthropic.Anthropic(api_key=api_key)
    print("  ✓ Authenticated\n")

    print("Step 1 — Scanning Gmail for LinkedIn alerts...")
    raw_jobs = fetch_linkedin_jobs(gmail)
    print(f"  ✓ {len(raw_jobs)} job(s) found across target cities")

    if not raw_jobs:
        print("  No jobs found in last 25h — nothing to do.")
        return

    print("\nStep 2 — Scoring and ranking by fit...")
    to_tailor, skipped = filter_and_rank_jobs(raw_jobs)
    print(f"  ✓ {len(to_tailor)} job(s) to tailor, {len(skipped)} filtered out")
    for j in to_tailor:
        print(
            f"    [{j['score']:>3}] {j['company']} — {j['title']} "
            f"({j['city']}) · {', '.join(j.get('reasons', []))}"
        )

    if not to_tailor:
        print("  No qualifying jobs — nothing to tailor.")
        return

    print("\nStep 3 — Tailoring CVs and saving to Drive...")
    folder_link_ref = {"value": f"https://drive.google.com/drive/search?q={DRIVE_FOLDER}"}
    results = [None] * len(to_tailor)

    def process(index: int, job: dict) -> dict:
        label = f"{job['company']} — {job['title']} ({job['city']}) [score:{job['score']}]"
        print(f"  [{index}/{len(to_tailor)}] {label}")
        try:
            tex = tailor_cv(claude_client, job)
            with drive_lock:
                link = save_to_drive(drive, docs, job, tex, today)
                folder_link_ref["value"] = "https://drive.google.com/drive/folders/" + link.split("/d/")[1].split("/")[0]
            print(f"       ✓ {link}")
            return {**job, "status": "tailored", "drive_link": link}
        except Exception as e:
            print(f"       ✗ Error: {e}")
            return {**job, "status": "error", "error_msg": str(e), "drive_link": None}

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(process, i + 1, job): i for i, job in enumerate(to_tailor)}
        for future in concurrent.futures.as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()

    if EMAIL_SENDING_ENABLED:
        print("\nStep 4 — Sending summary email...")
        try:
            send_summary_email(gmail, results, skipped, today, folder_link_ref["value"])
        except Exception as e:
            print(f"  ✗ Email failed: {e}")
    else:
        print("\nStep 4 — Email disabled. Set CV_TAILOR_SEND_EMAIL=true to enable.")

    n_tailored = sum(1 for r in results if r and r["status"] == "tailored")
    n_errors = sum(1 for r in results if r and r["status"] == "error")
    print(f"\n{'─' * 52}")
    print(f"  Done — {n_tailored}/{len(to_tailor)} CVs tailored, {len(skipped)} filtered out")
    print(f"{'─' * 52}\n")

    if n_errors:
        sys.exit(2)

if __name__ == "__main__":
    run()
