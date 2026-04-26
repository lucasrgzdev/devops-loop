"""
run.py — run one ticket through the full plan → code → review pipeline.

Usage:
    python scripts/run.py --project <name> --ticket <number>

Example:
    python scripts/run.py --project devops-loop --ticket 001

Requirements:
    ANTHROPIC_API_KEY env var must be set.
    Project must be registered: python scripts/init_db.py --add-project ...
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import anthropic

ROOT = Path(__file__).parent.parent
AGENTS_DIR = ROOT / "agents"
PROJECTS_DIR = ROOT / "projects"
RUNS_DIR = ROOT / "runs"
DB_PATH = ROOT / "db" / "projects.db"
FEEDBACK_LOG = ROOT / "feedback_log.md"

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4000

TREE_EXCLUDE = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", ".tox",
    "dist", "build", ".eggs",
}


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def git_pull(repo_path: Path) -> None:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "pull"],
        capture_output=True,
        text=True,
    )
    msg = result.stdout.strip() or result.stderr.strip() or "done"
    print(f"  git pull: {msg}")


def build_tree(root: Path, prefix: str = "", depth: int = 0, max_depth: int = 3) -> str:
    if depth > max_depth:
        return ""
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        entries = [e for e in entries if e.name not in TREE_EXCLUDE and not e.name.startswith(".")]
    except PermissionError:
        return ""
    lines = []
    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir() and depth < max_depth:
            extension = "    " if is_last else "│   "
            subtree = build_tree(entry, prefix + extension, depth + 1, max_depth)
            if subtree:
                lines.append(subtree)
    return "\n".join(lines)


def call_agent(
    client: anthropic.Anthropic,
    mra_content: str,
    agent_content: str,
    user_message: str,
) -> str:
    # MRA is cached — it's identical across all three calls and grows over time.
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": mra_content,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": agent_content,
            },
        ],
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


def find_ticket_file(project_name: str, ticket_number: str) -> Path:
    tickets_dir = PROJECTS_DIR / project_name / "tickets"
    if not tickets_dir.exists():
        raise FileNotFoundError(f"Tickets directory not found: {tickets_dir}")
    matches = list(tickets_dir.glob(f"{ticket_number.zfill(3)}-*.md"))
    if not matches:
        raise FileNotFoundError(
            f"No ticket matching '{ticket_number.zfill(3)}-*.md' in {tickets_dir}"
        )
    return matches[0]


def get_or_create_ticket(
    conn: sqlite3.Connection, project_id: int, filename: str, title: str
) -> int:
    row = conn.execute(
        "SELECT id FROM tickets WHERE project_id = ? AND filename = ?",
        (project_id, filename),
    ).fetchone()
    if row:
        conn.execute("UPDATE tickets SET status = 'running' WHERE id = ?", (row[0],))
        conn.commit()
        return row[0]
    cursor = conn.execute(
        "INSERT INTO tickets (project_id, filename, title, status) VALUES (?, ?, ?, 'running')",
        (project_id, filename, title),
    )
    conn.commit()
    return cursor.lastrowid


def save_run(
    conn: sqlite3.Connection,
    ticket_id: int,
    dev_output: str,
    review_output: str,
) -> int:
    cursor = conn.execute(
        "INSERT INTO runs (ticket_id, dev_output, review_output) VALUES (?, ?, ?)",
        (ticket_id, dev_output, review_output),
    )
    conn.commit()
    return cursor.lastrowid


def write_outputs(
    project_name: str,
    ticket_stem: str,
    planner_output: str,
    dev_output: str,
    review_output: str,
    run_id: int,
) -> Path:
    out_dir = RUNS_DIR / project_name / ticket_stem
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "planner_output.md").write_text(planner_output, encoding="utf-8")
    (out_dir / "dev_output.md").write_text(dev_output, encoding="utf-8")
    (out_dir / "review_output.md").write_text(review_output, encoding="utf-8")
    meta = {
        "run_id": run_id,
        "status": "pending",
        "approved": 0,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out_dir


def extract_verdict(review_output: str) -> str:
    for line in review_output.splitlines():
        stripped = line.strip()
        if "**Approve**" in stripped or stripped.startswith("Approve"):
            return "Approve"
        if "**Needs work**" in stripped or stripped.startswith("Needs work"):
            return "Needs work"
    return "(verdict not found — check review_output.md)"


def append_feedback_log(
    project_name: str, ticket_stem: str, run_id: int, verdict: str
) -> None:
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    entry = f"[{timestamp}] run#{run_id} | {project_name}/{ticket_stem} | {verdict}\n"
    with FEEDBACK_LOG.open("a", encoding="utf-8") as f:
        f.write(entry)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one ticket through the plan → code → review pipeline."
    )
    parser.add_argument("--project", required=True, help="Project name (must be registered in DB)")
    parser.add_argument("--ticket", required=True, help="Ticket number, e.g. 001")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT id, github_url, description, repo_path FROM projects WHERE name = ?",
        (args.project,),
    ).fetchone()
    if not row:
        raise SystemExit(
            f"Project '{args.project}' not found. "
            f"Register it first:\n  python scripts/init_db.py --add-project {args.project} --github-url <URL>"
        )
    project_id, github_url, description, repo_path_str = row
    repo_path = (ROOT / repo_path_str) if repo_path_str else None

    ticket_path = find_ticket_file(args.project, args.ticket)
    ticket_content = load_text(ticket_path)
    brief_path = PROJECTS_DIR / args.project / "brief.md"
    brief_content = load_text(brief_path) if brief_path.exists() else "(no brief.md found)"

    mra = load_text(AGENTS_DIR / "MRA.md")
    planner_agent = load_text(AGENTS_DIR / "PLANNER.md")
    dev_agent = load_text(AGENTS_DIR / "DEV.md")
    reviewer_agent = load_text(AGENTS_DIR / "REVIEWER.md")

    client = anthropic.Anthropic()

    print(f"\n{'─' * 50}")
    print(f"Project : {args.project}")
    print(f"Repo    : {github_url}")
    print(f"Ticket  : {ticket_path.name}")
    print(f"{'─' * 50}\n")

    tree_section = ""
    if repo_path and repo_path.exists():
        print("Pulling latest changes ...")
        git_pull(repo_path)
        tree = build_tree(repo_path)
        tree_section = f"\n\n## Codebase structure\n\n```\n{repo_path.name}/\n{tree}\n```"
        print()

    print("[1/3] Planner ...")
    planner_prompt = (
        f"## Project brief\n\n{brief_content}\n\n"
        f"## GitHub repo\n\n{github_url}"
        + (f"\n\n## Description\n\n{description}" if description else "")
        + tree_section
        + f"\n\n## Ticket\n\n{ticket_content}"
    )
    planner_output = call_agent(client, mra, planner_agent, planner_prompt)

    print("[2/3] Developer ...")
    dev_prompt = (
        f"## Ticket\n\n{ticket_content}\n\n"
        f"## Planning notes\n\n{planner_output}"
    )
    dev_output = call_agent(client, mra, dev_agent, dev_prompt)

    print("[3/3] Reviewer ...")
    reviewer_prompt = (
        f"## Ticket (alignment reference)\n\n{ticket_content}\n\n"
        f"## Developer output\n\n{dev_output}"
    )
    review_output = call_agent(client, mra, reviewer_agent, reviewer_prompt)

    ticket_id = get_or_create_ticket(conn, project_id, ticket_path.name, ticket_path.stem)
    run_id = save_run(conn, ticket_id, dev_output, review_output)
    out_dir = write_outputs(
        args.project, ticket_path.stem, planner_output, dev_output, review_output, run_id
    )

    verdict = extract_verdict(review_output)
    append_feedback_log(args.project, ticket_path.stem, run_id, verdict)

    conn.execute("UPDATE tickets SET status = 'done' WHERE id = ?", (ticket_id,))
    conn.commit()
    conn.close()

    print(f"\n{'─' * 50}")
    print(f"Run #{run_id} saved → {out_dir.relative_to(ROOT)}")
    print(f"Verdict : {verdict}")
    print(f"{'─' * 50}")


if __name__ == "__main__":
    main()
