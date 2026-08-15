"""Seed a demo database with realistic jobs, a CV profile, and chat history.

Usage:
    python scripts/seed_demo.py [--db PATH] [--force]

Defaults to ``data/searcher.db``. Use ``--force`` to drop existing rows first.
The goal is to populate every dashboard widget (analytics charts,
recommendations grid, chat coach, job detail) for screenshot generation
without depending on any LLM API key.
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import Database, make_job_hash  # noqa: E402

RNG = random.Random(42)


CV_MARKDOWN = """# Diego Riccardi — Junior Software Engineer

## Summary
Junior IT professional with hands-on experience in Python automation, web
scraping, and FastAPI services. Building a portfolio of AI-augmented tools
(LinkedIn Job Finder, chat coach, CV analyzer). Eager to join a product team
where I can grow across backend, data, and DevOps.

## Skills
- **Languages:** Python, JavaScript, TypeScript, SQL, Bash
- **Frameworks:** FastAPI, React, Node.js
- **Data:** SQLite, PostgreSQL, pandas
- **Cloud / DevOps:** Docker, Git, GitHub Actions, Linux
- **AI / ML:** Prompt engineering, LLM provider integration (Anthropic,
  OpenAI, Groq, Cerebras, Gemini, OpenRouter), RAG basics
- **Soft:** Agile, problem-solving, async communication

## Experience
**Independent Developer — Portfolio Projects (2024–present)**
- Built `LinkedIn Job Finder`: FastAPI + SQLite + multi-LLM provider
  abstraction, streamed SSE, Playwright E2E tests.
- Implemented CV ingestion (PDF/DOCX/TXT → markdown → structured profile).
- Designed a Notion/Stripe-inspired dashboard with i18n across 5 languages.

**IT Support Intern — Local SMB (2023)**
- Onboarded users, wrote PowerShell automation, maintained Windows fleet.

## Education
- ITS / Technical Institute — IT & Software track, graduated 2023.

## Interests
Open source, CTFs, reading about cognitive science and product design.
"""

CV_SUMMARY = {
    "skills": [
        "python", "typescript", "javascript", "react", "fastapi",
        "sql", "docker", "git", "linux", "api", "rest",
        "automation", "machine learning", "agile",
    ],
    "preferred_roles": [
        "Junior Python Developer",
        "Junior Full Stack Developer",
        "Junior Backend Engineer",
        "Junior Automation Engineer",
        "Junior AI Product Engineer",
    ],
    "experience_level": "junior",
    "years_experience": 2,
    "strengths": [
        "Ships end-to-end features solo",
        "Integrates multiple LLM providers with a clean abstraction",
        "Writes testable, typed Python",
    ],
    "industries": ["Software", "AI Tooling", "Developer Tools"],
    "education": "ITS diploma, IT & Software track",
    "summary": (
        "Junior full-stack developer with a Python/FastAPI backbone and a growing "
        "frontend skillset. Portfolio focuses on AI-assisted productivity tools."
    ),
    "graduation_year": "2023",
}


JOBS: list[dict] = [
    # High-score matches (candidati subito)
    {
        "titolo": "Junior Python Backend Engineer", "azienda": "Stripe",
        "sede": "Remote - EU", "fonte": "LinkedIn", "modalita": "Remote",
        "descrizione": "Build internal tooling in Python/FastAPI. Work with SQL, event streams, and a modern CI pipeline. Mentorship from senior engineers.",
        "score": 10, "consiglio": "Candidati subito", "status": "open", "favorite": True, "days_ago": 1,
        "junior_friendly": "Si", "anni_esperienza": "0-2",
        "strengths": "Stack match perfetto: Python, FastAPI, SQL. Cultura remote-friendly.",
        "weaknesses": "Alto livello di competizione, processo di selezione lungo.",
        "ral": "45k-55k EUR", "reputazione": "Eccellente", "note": "Top fintech, career-defining opportunity.",
    },
    {
        "titolo": "AI Product Engineer", "azienda": "Anthropic",
        "sede": "Remote - Global", "fonte": "Company site", "modalita": "Remote",
        "descrizione": "Ship user-facing AI features using Claude. Evaluate prompts, measure output quality, iterate on UX. Strong Python + TS required.",
        "score": 9, "consiglio": "Candidati subito", "status": "applied", "favorite": True, "days_ago": 3,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "Hai gia' integrato Claude nel portfolio. Ottimo culture fit.",
        "weaknesses": "Richiede solid ML intuition, profilo altamente ricercato.",
        "ral": "60k-80k USD", "reputazione": "Eccellente", "note": "Frontier AI lab.",
    },
    {
        "titolo": "Junior Full Stack Developer", "azienda": "Linear",
        "sede": "Remote - EU", "fonte": "LinkedIn", "modalita": "Remote",
        "descrizione": "React + TypeScript frontend, Node backend. Ship features end-to-end. Strong design sensibility valued.",
        "score": 9, "consiglio": "Candidati subito", "status": "interviewing", "favorite": True, "days_ago": 5,
        "junior_friendly": "Si", "anni_esperienza": "1-2",
        "strengths": "Design system/UX focus aligns with your portfolio's Notion-style approach.",
        "weaknesses": "Product complexity alta, onboarding curve.",
        "ral": "50k-65k EUR", "reputazione": "Eccellente", "note": "Design-driven engineering culture.",
    },
    {
        "titolo": "Backend Developer (Python)", "azienda": "Shopify",
        "sede": "Remote - EMEA", "fonte": "Indeed", "modalita": "Remote",
        "descrizione": "Scale merchant-facing APIs. Python, Ruby, Postgres. Greenfield projects in checkout/data.",
        "score": 9, "consiglio": "Candidati subito", "status": "open", "favorite": True, "days_ago": 2,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "Python core matches, scale experience would accelerate growth.",
        "weaknesses": "Richiede Ruby familiarity (non sul tuo CV).",
        "ral": "55k-70k EUR", "reputazione": "Ottima", "note": "E-commerce giant, stable.",
    },
    {
        "titolo": "Junior Automation Engineer", "azienda": "Notion",
        "sede": "Remote - EU", "fonte": "LinkedIn", "modalita": "Remote",
        "descrizione": "Automate internal workflows, data pipelines, Playwright E2E, DX tooling.",
        "score": 8, "consiglio": "Candidati subito", "status": "open", "favorite": False, "days_ago": 4,
        "junior_friendly": "Si", "anni_esperienza": "0-2",
        "strengths": "Portfolio gia' include Playwright E2E e automation scripts.",
        "weaknesses": "Team piccolo, aspettative alte di autonomia.",
        "ral": "50k-60k EUR", "reputazione": "Eccellente", "note": "Collaborative docs leader.",
    },
    # Mid-range (valutabile)
    {
        "titolo": "Full Stack Developer (Python + React)", "azienda": "Vercel",
        "sede": "Remote - Global", "fonte": "LinkedIn", "modalita": "Remote",
        "descrizione": "Work across Next.js, Python serverless, developer tooling. Shape DX for millions of developers.",
        "score": 8, "consiglio": "Valutabile", "status": "applied", "favorite": False, "days_ago": 7,
        "junior_friendly": "Si", "anni_esperienza": "2-4",
        "strengths": "Stack allineato, DX focus matches portfolio.",
        "weaknesses": "Richiede Next.js production experience.",
        "ral": "70k+ USD", "reputazione": "Eccellente", "note": "Next.js creators.",
    },
    {
        "titolo": "Junior Data Engineer", "azienda": "Figma",
        "sede": "Hybrid - London", "fonte": "LinkedIn", "modalita": "Hybrid",
        "descrizione": "Build pipelines, dbt models, Airflow DAGs. Work with design/product analytics.",
        "score": 7, "consiglio": "Valutabile", "status": "open", "favorite": False, "days_ago": 6,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "Python + SQL fit. Visual product aligns with portfolio taste.",
        "weaknesses": "Airflow/dbt non ancora nel CV, Londra location.",
        "ral": "55k-70k GBP", "reputazione": "Eccellente", "note": "Design tool leader.",
    },
    {
        "titolo": "Software Engineer - Platform", "azienda": "GitHub",
        "sede": "Remote - EU", "fonte": "LinkedIn", "modalita": "Remote",
        "descrizione": "Platform services in Go and Ruby. Help internal teams ship faster.",
        "score": 7, "consiglio": "Valutabile", "status": "open", "favorite": False, "days_ago": 8,
        "junior_friendly": "No", "anni_esperienza": "3+",
        "strengths": "Platform work is a natural evolution of your tooling interest.",
        "weaknesses": "Go e Ruby non in CV, seniority richiesta piu' alta.",
        "ral": "80k+ USD", "reputazione": "Eccellente", "note": "Microsoft-owned, solid benefits.",
    },
    {
        "titolo": "Backend Engineer - Data Platform", "azienda": "DuckDB Labs",
        "sede": "Remote - EU", "fonte": "Company site", "modalita": "Remote",
        "descrizione": "C++ and Python bindings for analytical database. Performance-critical work.",
        "score": 7, "consiglio": "Valutabile", "status": "open", "favorite": False, "days_ago": 9,
        "junior_friendly": "No", "anni_esperienza": "2-5",
        "strengths": "Python bindings work leverages your strengths.",
        "weaknesses": "C++ richiesto, non nel CV.",
        "ral": "65k-85k EUR", "reputazione": "Ottima", "note": "Analytical DB, cutting edge.",
    },
    {
        "titolo": "Junior DevOps Engineer", "azienda": "Cloudflare",
        "sede": "Remote - EMEA", "fonte": "LinkedIn", "modalita": "Remote",
        "descrizione": "Terraform, Kubernetes, CI/CD pipelines. Support edge infrastructure teams.",
        "score": 6, "consiglio": "Valutabile", "status": "open", "favorite": False, "days_ago": 10,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "Linux/Docker/Git gia' nel CV.",
        "weaknesses": "Kubernetes e Terraform non ancora padroneggiati.",
        "ral": "50k-65k EUR", "reputazione": "Ottima", "note": "Edge network leader.",
    },
    {
        "titolo": "Python Developer - Fintech", "azienda": "Scalapay",
        "sede": "Hybrid - Milan", "fonte": "LinkedIn", "modalita": "Hybrid",
        "descrizione": "Build BNPL backend services in Python. Fast-paced scale-up environment.",
        "score": 7, "consiglio": "Valutabile", "status": "open", "favorite": True, "days_ago": 3,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "Python match, vicino a Torino (Milano hybrid).",
        "weaknesses": "Dominio fintech nuovo, pace elevata.",
        "ral": "40k-55k EUR", "reputazione": "Buona", "note": "Italian scale-up, expanding EU.",
    },
    {
        "titolo": "Junior AI Engineer", "azienda": "Mistral AI",
        "sede": "Remote - EU", "fonte": "Company site", "modalita": "Remote",
        "descrizione": "Work on open-weights LLM infrastructure, eval pipelines, developer-facing tooling.",
        "score": 8, "consiglio": "Valutabile", "status": "applied", "favorite": True, "days_ago": 5,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "LLM tooling experience from your portfolio transfers directly.",
        "weaknesses": "Profilo molto ricercato, competitivo.",
        "ral": "55k-75k EUR", "reputazione": "Eccellente", "note": "EU frontier lab.",
    },
    # Lower-score / mismatches
    {
        "titolo": "Senior Backend Engineer (Go)", "azienda": "Datadog",
        "sede": "Remote - EMEA", "fonte": "LinkedIn", "modalita": "Remote",
        "descrizione": "Distributed systems in Go. 5+ years of backend experience required.",
        "score": 5, "consiglio": "Non adatto", "status": "rejected", "favorite": False, "days_ago": 12,
        "junior_friendly": "No", "anni_esperienza": "5+",
        "strengths": "Observability domain interessante.",
        "weaknesses": "Seniority richiesta troppo alta, Go non in CV.",
        "ral": "90k+ EUR", "reputazione": "Eccellente", "note": "Observability leader.",
    },
    {
        "titolo": ".NET Developer - Enterprise", "azienda": "Accenture",
        "sede": "Hybrid - Turin", "fonte": "Indeed", "modalita": "Hybrid",
        "descrizione": "C#/.NET backend, legacy migrations, banking clients.",
        "score": 4, "consiglio": "Non adatto", "status": "archived", "favorite": False, "days_ago": 14,
        "junior_friendly": "Si", "anni_esperienza": "0-3",
        "strengths": "Locale (Torino), entry-level ok.",
        "weaknesses": "Stack .NET non e' obiettivo di carriera, consulenza body-rental.",
        "ral": "28k-35k EUR", "reputazione": "Discreta", "note": "Large consultancy.",
    },
    {
        "titolo": "SAP Consultant Junior", "azienda": "Reply",
        "sede": "On-site - Turin", "fonte": "Indeed", "modalita": "On-site",
        "descrizione": "SAP FICO module implementation, client-facing consulting.",
        "score": 3, "consiglio": "Non adatto", "status": "rejected", "favorite": False, "days_ago": 16,
        "junior_friendly": "Si", "anni_esperienza": "0-2",
        "strengths": "Local position.",
        "weaknesses": "Stack completamente disallineato (SAP, ERP).",
        "ral": "26k-32k EUR", "reputazione": "Buona", "note": "Italian consultancy.",
    },
    # More open jobs for volume
    {
        "titolo": "Junior Software Engineer - Platform", "azienda": "Deliveroo",
        "sede": "Remote - EU", "fonte": "LinkedIn", "modalita": "Remote",
        "descrizione": "Python microservices, order pipeline, logistics optimization.",
        "score": 7, "consiglio": "Valutabile", "status": "open", "favorite": False, "days_ago": 11,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "Python + distributed systems exposure.",
        "weaknesses": "Dominio logistica nuovo.",
        "ral": "45k-60k EUR", "reputazione": "Buona", "note": "Food delivery leader.",
    },
    {
        "titolo": "Backend Developer - SaaS", "azienda": "Fastweb",
        "sede": "Hybrid - Milan", "fonte": "LinkedIn", "modalita": "Hybrid",
        "descrizione": "FastAPI services, Postgres, internal customer portals.",
        "score": 7, "consiglio": "Valutabile", "status": "open", "favorite": False, "days_ago": 6,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "FastAPI match esatto, Italia-based.",
        "weaknesses": "Grande corporate, processi lenti.",
        "ral": "35k-45k EUR", "reputazione": "Ottima", "note": "Italian telco.",
    },
    {
        "titolo": "Software Engineer - AI Tooling", "azienda": "Hugging Face",
        "sede": "Remote - Global", "fonte": "Company site", "modalita": "Remote",
        "descrizione": "Open-source ML infrastructure, Python SDKs, developer tooling.",
        "score": 8, "consiglio": "Valutabile", "status": "open", "favorite": True, "days_ago": 2,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "Open source + ML tooling aligns perfectly.",
        "weaknesses": "Profilo ricercato, competitive bar.",
        "ral": "60k-90k USD", "reputazione": "Eccellente", "note": "ML community hub.",
    },
    {
        "titolo": "Full Stack Engineer (TS + Python)", "azienda": "Supabase",
        "sede": "Remote - Global", "fonte": "LinkedIn", "modalita": "Remote",
        "descrizione": "Open-source Firebase alternative. TypeScript, Deno, Postgres. Community-driven.",
        "score": 8, "consiglio": "Valutabile", "status": "open", "favorite": False, "days_ago": 7,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "Open source culture, TS + Postgres fit.",
        "weaknesses": "Async-first remote richiede disciplina alta.",
        "ral": "60k-85k USD", "reputazione": "Ottima", "note": "OSS-first startup.",
    },
    {
        "titolo": "Junior QA Automation Engineer", "azienda": "Prima Assicurazioni",
        "sede": "Hybrid - Milan", "fonte": "LinkedIn", "modalita": "Hybrid",
        "descrizione": "Playwright, Cypress, API test automation.",
        "score": 6, "consiglio": "Valutabile", "status": "open", "favorite": False, "days_ago": 9,
        "junior_friendly": "Si", "anni_esperienza": "0-2",
        "strengths": "Playwright gia' nel CV.",
        "weaknesses": "Dominio assicurativo, QA career track non primario.",
        "ral": "30k-40k EUR", "reputazione": "Buona", "note": "Italian insurtech.",
    },
    {
        "titolo": "DevOps Engineer Junior", "azienda": "Satispay",
        "sede": "Hybrid - Milan", "fonte": "LinkedIn", "modalita": "Hybrid",
        "descrizione": "AWS, Terraform, k8s, CI/CD. Fintech fast pace.",
        "score": 6, "consiglio": "Valutabile", "status": "open", "favorite": False, "days_ago": 10,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "Linux/Docker base, Italian fintech scale.",
        "weaknesses": "AWS/Terraform da colmare.",
        "ral": "35k-48k EUR", "reputazione": "Ottima", "note": "Leading Italian fintech.",
    },
    {
        "titolo": "Python Backend - Data Science Tooling", "azienda": "Posit (RStudio)",
        "sede": "Remote - Global", "fonte": "Company site", "modalita": "Remote",
        "descrizione": "Build Python tooling for data scientists. Jupyter, Quarto, ML workflows.",
        "score": 7, "consiglio": "Valutabile", "status": "open", "favorite": False, "days_ago": 8,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "Python + DX tooling match.",
        "weaknesses": "Ecosistema R/Jupyter nuovo.",
        "ral": "55k-75k USD", "reputazione": "Ottima", "note": "Data science tools.",
    },
    {
        "titolo": "Software Engineer - Growth", "azienda": "Revolut",
        "sede": "Remote - EU", "fonte": "LinkedIn", "modalita": "Remote",
        "descrizione": "Experimentation platform, A/B testing, Python/Java backend.",
        "score": 6, "consiglio": "Valutabile", "status": "archived", "favorite": False, "days_ago": 18,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "Python match, scale opportunity.",
        "weaknesses": "Cultura high-intensity, Java non in CV.",
        "ral": "50k-70k EUR", "reputazione": "Ottima", "note": "European fintech super-app.",
    },
    {
        "titolo": "Junior Full Stack - Greentech", "azienda": "Octopus Energy",
        "sede": "Remote - EU", "fonte": "LinkedIn", "modalita": "Remote",
        "descrizione": "Kraken platform: Python + TypeScript. Energy transition impact.",
        "score": 7, "consiglio": "Valutabile", "status": "open", "favorite": False, "days_ago": 4,
        "junior_friendly": "Si", "anni_esperienza": "1-3",
        "strengths": "Stack match, mission-driven culture.",
        "weaknesses": "Dominio energia regolato, complessita'.",
        "ral": "40k-55k EUR", "reputazione": "Ottima", "note": "Greentech energy retailer.",
    },
    {
        "titolo": "Automation Specialist - RPA", "azienda": "TIM",
        "sede": "On-site - Turin", "fonte": "Indeed", "modalita": "On-site",
        "descrizione": "UiPath, Python scripts, internal process automation.",
        "score": 4, "consiglio": "Non adatto", "status": "archived", "favorite": False, "days_ago": 20,
        "junior_friendly": "Si", "anni_esperienza": "0-2",
        "strengths": "Locale Torino.",
        "weaknesses": "RPA stack stretto, on-site, stack legacy.",
        "ral": "28k-34k EUR", "reputazione": "Discreta", "note": "Italian telco incumbent.",
    },
]


CHAT_MESSAGES = [
    ("user", "Hi! Can you review my CV and tell me which roles fit best?"),
    ("assistant",
     "Looked at your profile. You have a strong Python + FastAPI backbone plus "
     "hands-on LLM integration experience — that is a rare combo for a junior "
     "candidate. The three best-fit roles right now are: 1) Junior Python "
     "Backend Engineer (Stripe, Shopify), 2) AI Product Engineer (Anthropic, "
     "Hugging Face), and 3) Junior Full Stack (Linear, Supabase). I marked five "
     "jobs as Candidati subito in your dashboard."),
    ("user", "What should I prioritize to level up in the next 3 months?"),
    ("assistant",
     "Three focused moves: (1) Ship one more AI-assisted side project with a "
     "clean README and tests — recruiters skim GitHub in 30 seconds. "
     "(2) Get comfortable with Next.js / React Server Components — most "
     "full-stack listings assume it. (3) Write two short blog posts about "
     "decisions in LinkedIn Job Finder (why SQLite, why multi-provider "
     "abstraction). Technical writing is a hiring multiplier."),
    ("user", "How should I approach the Anthropic application?"),
    ("assistant",
     "Lead with the fact that you already integrated Claude in a shipped "
     "product. Concrete numbers help: response latency, prompt caching hit "
     "rate, eval results. Attach a short Loom (3 min) walking through your "
     "chat coach. For the cover letter, skip the boilerplate and open with "
     "the problem you solved and the tradeoff you picked."),
    ("user", "Any interview prep tips for the Linear role?"),
    ("assistant",
     "Linear weighs product taste heavily. Before the interview: use Linear "
     "for a week, note three UX decisions you admire and one you would "
     "change. Review their engineering blog — they are big on local-first and "
     "graph sync. Coding exercise is usually TS-heavy, so brush up on "
     "discriminated unions and async patterns."),
]


# What a user who has already run one scan would have on file. Without these the
# readiness gate blocks the search screen and every screenshot shows the gate.
DEMO_TERMS = ["python backend engineer", "ai product engineer", "data analyst"]
DEMO_LOCATIONS = ["Milan", "Remote - EU"]

#: The demo mailbox account. Nothing is fetched: these rows stand for messages
#: the watcher already read, so the Mail tab, its badge and the review queue have
#: something to show. Addresses are fictional on purpose.
DEMO_MAIL_ACCOUNT = "demo@example.com"

#: Proposals waiting for a decision. `attach` = a confirmation that could belong
#: to an offer already in the archive; `import` = an application to a company the
#: archive never knew about, which becomes a new row if the user says so.
MAIL_REVIEW: list[dict] = [
    {
        "kind": "attach", "company": "Stripe", "role": "Junior Python Backend Engineer",
        "sender": "no-reply@stripe.example", "rule": "subject:application received",
        "days_ago": 1, "match_titles": ["Junior Python Backend Engineer"],
    },
    {
        "kind": "attach", "company": "Anthropic", "role": "AI Product Engineer",
        "sender": "careers@anthropic.example", "rule": "body:we received your application",
        "days_ago": 2, "match_titles": ["AI Product Engineer"],
    },
    {
        "kind": "import", "company": "Zalando", "role": "Backend Engineer (Platform)",
        "sender": "jobs@zalando.example", "rule": "body:thank you for applying",
        "days_ago": 4, "match_titles": [],
    },
]


def insert_mail(db: Database) -> None:
    """Messages the watcher has already read, and the ones still to decide."""
    now = datetime.now(UTC)
    by_title = {
        str(row["titolo"]): int(row["id"])
        for row in db.conn.execute("SELECT id, titolo FROM jobs").fetchall()
    }
    for index, item in enumerate(MAIL_REVIEW):
        received = now - timedelta(days=item["days_ago"])
        db.add_mail_review(
            account=DEMO_MAIL_ACCOUNT,
            mail_key=f"demo-review-{index}",
            kind=item["kind"],
            message_id=f"<demo-{index}@example.com>",
            received_at=now_iso(received),
            company=item["company"],
            sender=item["sender"],
            rule=item["rule"],
            role=item["role"],
            candidates=[by_title[t] for t in item["match_titles"] if t in by_title],
        )
    # A handful of already-decided messages, so the tab does not read as if the
    # mailbox had never been checked.
    for index in range(6):
        db.conn.execute(
            "INSERT INTO mail_seen (account, mail_key, message_id, received_at, verdict, "
            "job_id, matched_rule, seen_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                DEMO_MAIL_ACCOUNT,
                f"demo-seen-{index}",
                f"<demo-seen-{index}@example.com>",
                now_iso(now - timedelta(days=index + 2)),
                "ignored" if index % 2 else "newsletter",
                "sender:newsletter",
                now_iso(now - timedelta(days=index + 2)),
            ),
        )
    db.conn.commit()


def now_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def wipe(conn: sqlite3.Connection) -> None:
    # Every table this script writes, plus the ones a previous run of the app
    # could have left behind. Wiping seven of sixteen left mail proposals and
    # saved searches from an older seed sitting in the "fresh" demo.
    for table in (
        "job_actions", "chat_messages", "preferences",
        "candidate_profiles", "scan_runs", "jobs", "usage_log",
        "mail_seen", "mail_review", "saved_searches", "score_feedback",
        "watchlist_companies", "chat_sessions", "pinned_jobs", "recruiters",
    ):
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.DatabaseError:
            pass  # a table this schema version does not have yet
    conn.commit()


def insert_jobs(db: Database) -> None:
    conn = db.conn
    cur = conn.cursor()
    now = datetime.now(UTC)
    for idx, job in enumerate(JOBS):
        link = f"https://example.com/jobs/{idx + 1}-{job['azienda'].lower().replace(' ', '-')}"
        seen = now - timedelta(days=job["days_ago"], hours=RNG.randint(0, 12))
        first_seen = seen - timedelta(hours=RNG.randint(1, 6))
        hash_value = make_job_hash(job["titolo"], job["azienda"], link)

        analysis = {
            "punteggio": job["score"],
            "consiglio": job["consiglio"],
            "programmazione_richiesta": "Si",
            "smart_working": "Si" if "Remote" in job["modalita"] or "Hybrid" in job["modalita"] else "No",
            "contratto": "Indeterminato",
            "junior_friendly": job["junior_friendly"],
            "anni_esperienza_richiesti": job["anni_esperienza"],
            "punti_forza": job["strengths"],
            "punti_deboli": job["weaknesses"],
            "riassunto": job["descrizione"][:200],
            "stipendio_min": "",
            "stipendio_max": "",
            "ral_stimata": job["ral"],
            "reputazione_azienda": job["reputazione"],
            "adatta_neolaureati": job["junior_friendly"],
            "note_azienda": job["note"],
            "pros": [job["strengths"]],
            "cons": [job["weaknesses"]],
            "fit_reasons": [job["strengths"]],
        }

        cur.execute(
            """
            INSERT INTO jobs(
                job_hash, titolo, azienda, descrizione, sede, fonte, link,
                ricerca_usata, modalita, analysis_json, punteggio_ai, consiglio,
                status, is_favorite, is_new,
                first_seen_at, last_seen_at, analyzed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hash_value, job["titolo"], job["azienda"], job["descrizione"],
                job["sede"], job["fonte"], link, "Junior Python Developer",
                job["modalita"], json.dumps(analysis, ensure_ascii=False),
                job["score"], job["consiglio"], job["status"],
                1 if job["favorite"] else 0,
                1 if job["days_ago"] <= 2 else 0,
                now_iso(first_seen), now_iso(seen), now_iso(seen), now_iso(seen),
            ),
        )
        job_id = cur.lastrowid
        if job["status"] in ("applied", "interviewing", "rejected"):
            cur.execute(
                "INSERT INTO job_actions(job_id, action, notes, created_at) VALUES (?, ?, ?, ?)",
                (job_id, job["status"], "", now_iso(seen)),
            )
    conn.commit()


def insert_scan_runs(db: Database) -> None:
    now = datetime.now(UTC)
    runs = [
        (now - timedelta(days=14), "Italy", True, ["Junior Python Developer", "Junior Full Stack Developer"], 18, 18, 16, 2),
        (now - timedelta(days=7), "Italy", True, ["Junior Python Developer", "AI Engineer"], 14, 9, 9, 0),
        (now - timedelta(days=1), "Italy", True, ["Junior Python Developer", "Junior Backend"], 11, 4, 4, 0),
    ]
    for started, location, remote, terms, found, new_c, analyzed, discarded in runs:
        finished = started + timedelta(minutes=RNG.randint(3, 8))
        db.conn.execute(
            """
            INSERT INTO scan_runs(started_at, finished_at, location, is_remote,
                terms_json, totale_trovati, totale_nuovi, totale_analizzati, totale_scartati)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now_iso(started), now_iso(finished), location, 1 if remote else 0,
             json.dumps(terms), found, new_c, analyzed, discarded),
        )
    db.conn.commit()


def insert_chat(db: Database) -> None:
    now = datetime.now(UTC)
    session_id = "default"
    for i, (role, content) in enumerate(CHAT_MESSAGES):
        ts = now - timedelta(minutes=(len(CHAT_MESSAGES) - i) * 2)
        db.conn.execute(
            "INSERT INTO chat_messages(session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_id, role, content, now_iso(ts)),
        )
    db.conn.commit()


def insert_usage_log(db: Database) -> None:
    """Populate usage_log so the dashboard 'AI Usage' panel shows real numbers."""
    now = datetime.now(UTC)
    # (provider, model, endpoint, days_ago, calls, avg_prompt, avg_completion)
    rows = [
        ("openrouter", "openai/gpt-oss-120b:free", "chat", 0, 8, 900, 350),
        ("openrouter", "openai/gpt-oss-120b:free", "complete_json", 0, 18, 1200, 300),
        ("groq", "llama-3.3-70b-versatile", "chat", 0, 5, 800, 400),
        ("cerebras", "qwen-3-235b", "complete_json", 1, 12, 1100, 280),
        ("anthropic", "claude-sonnet", "chat", 2, 3, 1500, 600),
        ("openrouter", "openai/gpt-oss-120b:free", "complete_json", 5, 22, 1150, 290),
        ("groq", "llama-3.3-70b-versatile", "chat", 6, 4, 850, 380),
    ]
    for provider, model, endpoint, days_ago, calls, ap, ac in rows:
        for _ in range(calls):
            if days_ago == 0:
                ts = now - timedelta(minutes=RNG.randint(2, 180))
            else:
                ts = now - timedelta(days=days_ago, hours=RNG.randint(0, 20))
            pt = max(1, ap + RNG.randint(-150, 150))
            ct = max(1, ac + RNG.randint(-80, 80))
            db.conn.execute(
                """INSERT INTO usage_log(ts, provider, model, endpoint, prompt_tokens,
                   completion_tokens, total_tokens, success, error_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now_iso(ts), provider, model, endpoint, pt, ct, pt + ct, 1, None),
            )
    db.conn.commit()


def insert_sample_note(db: Database) -> None:
    """Add a note to a job's timeline so the detail panel demo isn't empty."""
    # Target the top open job (a top recommendation) so the detail-panel timeline
    # demo — opened from the dashboard recommendations — shows a real note.
    row = db.conn.execute(
        "SELECT id FROM jobs WHERE status = 'open' ORDER BY punteggio_ai DESC LIMIT 1"
    ).fetchone()
    if not row:
        return
    db.conn.execute(
        "INSERT INTO job_actions(job_id, action, notes, created_at) VALUES (?, 'note', ?, ?)",
        (
            row[0],
            "Recruiter call scheduled for Thursday 3pm. Prep: local-first architecture questions.",
            now_iso(datetime.now(UTC)),
        ),
    )
    db.conn.commit()


def insert_profile_and_prefs(db: Database) -> None:
    profile_id = db.save_candidate_profile(
        source_name="CV_Diego_Riccardi_EN.pdf",
        markdown=CV_MARKDOWN,
        summary=CV_SUMMARY,
    )
    db.set_preference("active_profile_id", str(profile_id))
    db.set_preference("language", "en")
    db.set_preference("theme", "light")
    db.set_preference("location_default", "Italy")
    db.set_preference("chat_session_id", "default")
    # Without these two the readiness gate blocks the search screen, and the
    # screenshot shows the gate instead of the form. They are what a user who
    # has already run one scan would have.
    db.set_preference("last_scan_terms", json.dumps(DEMO_TERMS, ensure_ascii=False))
    db.set_preference("last_scan_locations", json.dumps(DEMO_LOCATIONS, ensure_ascii=False))
    db.set_preference("preferred_roles", ", ".join(DEMO_TERMS))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # NOT the real archive. The default used to be data/searcher.db, so a
    # distracted `--force` wiped a year of real applications to make screenshots.
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "demo.db")
    parser.add_argument("--force", action="store_true", help="Wipe existing rows first")
    args = parser.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    print(f"Seeding demo data into: {args.db}")

    db = Database(args.db)
    try:
        if args.force:
            wipe(db.conn)
            print("  Existing rows wiped.")

        insert_profile_and_prefs(db)
        print("  Inserted candidate profile + preferences.")
        insert_jobs(db)
        print(f"  Inserted {len(JOBS)} jobs.")
        insert_scan_runs(db)
        print("  Inserted 3 scan runs.")
        insert_chat(db)
        print(f"  Inserted {len(CHAT_MESSAGES)} chat messages.")
        insert_usage_log(db)
        print("  Inserted usage_log rows (AI Usage panel).")
        insert_sample_note(db)
        print("  Inserted a sample job note (timeline).")
        insert_mail(db)
        print(f"  Inserted {len(MAIL_REVIEW)} mail proposals + seen messages.")

        analytics = db.get_analytics()
        print("\nResult summary:")
        print(f"  Total jobs: {analytics['total']}")
        print(f"  By status: {analytics['jobs_by_status']}")
        print(f"  Favorites: {sum(1 for j in JOBS if j['favorite'])}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
