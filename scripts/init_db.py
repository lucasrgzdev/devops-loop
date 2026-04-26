"""
init_db.py — create the database tables and register projects.

Usage:
    python scripts/init_db.py                                   # create tables only
    python scripts/init_db.py --add-project my-app \
        --github-url https://github.com/you/my-app \
        --description "What this repo does"
    python scripts/init_db.py --list                            # show registered projects
    python scripts/init_db.py --add-ticket "my ticket title" \
        --project my-app \
        --body "## Goal\nDo the thing.\n\n## Acceptance criteria\n- [ ] It works"
"""
from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "db" / "projects.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    github_url  TEXT NOT NULL,
    repo_path   TEXT,
    description TEXT,
    status      TEXT DEFAULT 'active',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER REFERENCES projects(id),
    filename    TEXT,
    title       TEXT,
    status      TEXT DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id       INTEGER REFERENCES tickets(id),
    dev_output      TEXT,
    review_output   TEXT,
    approved        INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER REFERENCES runs(id),
    note        TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_SCHEMA)
    conn.commit()
    print(f"Database ready: {DB_PATH}")
    return conn


def add_project(
    name: str,
    github_url: str,
    repo_path: str | None = None,
    description: str | None = None,
) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO projects (name, github_url, repo_path, description) VALUES (?, ?, ?, ?)",
            (name, github_url, repo_path, description),
        )
        conn.commit()
        print(f"Registered: '{name}' → {github_url}")
    except sqlite3.IntegrityError:
        print(f"Project '{name}' already exists — skipped.")
    finally:
        conn.close()


def _slugify(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _next_ticket_number(tickets_dir: Path) -> str:
    existing = [p.name for p in tickets_dir.glob("*.md")]
    numbers = []
    for name in existing:
        m = re.match(r"^(\d+)-", name)
        if m:
            numbers.append(int(m.group(1)))
    return str(max(numbers, default=0) + 1).zfill(3)


_TICKET_TEMPLATE = """\
## Goal
{title}

## Acceptance criteria
- [ ]

## Notes
"""


def add_ticket(
    project_name: str,
    title: str,
    body: str | None = None,
) -> None:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id FROM projects WHERE name = ?", (project_name,)
    ).fetchone()
    if not row:
        print(f"Project '{project_name}' not found — register it first with --add-project.")
        conn.close()
        return
    project_id = row[0]

    tickets_dir = Path(__file__).parent.parent / "projects" / project_name / "tickets"
    tickets_dir.mkdir(parents=True, exist_ok=True)

    number = _next_ticket_number(tickets_dir)
    slug = _slugify(title)
    filename = f"{number}-{slug}.md"
    content = body if body is not None else _TICKET_TEMPLATE.format(title=title)

    ticket_path = tickets_dir / filename
    ticket_path.write_text(content, encoding="utf-8")

    conn.execute(
        "INSERT INTO tickets (project_id, filename, title, status) VALUES (?, ?, ?, 'open')",
        (project_id, filename, title),
    )
    conn.commit()
    conn.close()
    print(f"Ticket created: {ticket_path}")


def list_projects() -> None:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT name, github_url, status, description FROM projects ORDER BY id"
    ).fetchall()
    conn.close()
    if not rows:
        print("No projects registered yet.")
        return
    for name, url, status, desc in rows:
        print(f"  [{status}] {name}")
        print(f"          {url}")
        if desc:
            print(f"          {desc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialise the devops-loop database.")
    parser.add_argument("--add-project", metavar="NAME", help="Register a GitHub repo as a project")
    parser.add_argument("--github-url", metavar="URL", help="Full GitHub URL (required with --add-project)")
    parser.add_argument("--repo-path", metavar="PATH", help="Local clone path (optional)")
    parser.add_argument("--description", metavar="TEXT", help="One-line project description")
    parser.add_argument("--list", action="store_true", help="List registered projects")
    parser.add_argument("--add-ticket", metavar="TITLE", help="Create a ticket for a project")
    parser.add_argument("--project", metavar="NAME", help="Project name (required with --add-ticket)")
    parser.add_argument("--body", metavar="TEXT", help="Ticket body markdown (optional; uses template if omitted)")
    args = parser.parse_args()

    init_db()

    if args.add_project:
        if not args.github_url:
            parser.error("--github-url is required with --add-project")
        add_project(args.add_project, args.github_url, args.repo_path, args.description)

    if args.add_ticket:
        if not args.project:
            parser.error("--project is required with --add-ticket")
        add_ticket(args.project, args.add_ticket, args.body)

    if args.list:
        list_projects()


if __name__ == "__main__":
    main()
