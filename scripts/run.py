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
import re
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
MAX_TOOL_CALLS = 10
MAX_FILE_CHARS = 8_000

READ_FILE_TOOL = {
    "name": "read_file",
    "description": (
        "Read a file from the project repository. "
        "Call this before writing code that touches an existing file. "
        "Prefer the files the Planner identified as relevant."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relative to the repo root, e.g. 'scripts/run.py'",
            }
        },
        "required": ["path"],
    },
}

WRITE_FILE_TOOL = {
    "name": "write_file",
    "description": (
        "Write content to a file in the project repository. "
        "Creates the file if it doesn't exist, overwrites if it does. "
        "Always read the existing file first before overwriting. "
        "Use this to apply your code changes directly — do NOT just output code blocks."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relative to the repo root, e.g. 'scripts/run.py'",
            },
            "content": {
                "type": "string",
                "description": "The complete file content to write.",
            },
        },
        "required": ["path", "content"],
    },
}

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


def git_get_branch(repo_path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() or "main"


def git_create_branch(repo_path: Path, branch: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo_path), "checkout", "-b", branch],
        capture_output=True, text=True,
    )


def git_stage_and_diff(repo_path: Path) -> str:
    subprocess.run(["git", "-C", str(repo_path), "add", "-A"], capture_output=True)
    result = subprocess.run(
        ["git", "-C", str(repo_path), "diff", "--cached"],
        capture_output=True, text=True,
    )
    return result.stdout


def git_commit_run(repo_path: Path, ticket_stem: str, run_id: int) -> None:
    msg = f"chore(devops-loop): run #{run_id} — {ticket_stem}"
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", msg],
        capture_output=True, text=True,
    )


def git_revert_and_restore(repo_path: Path, original_branch: str) -> None:
    """Discard all staged/unstaged changes and return to original_branch."""
    subprocess.run(["git", "-C", str(repo_path), "reset", "HEAD"], capture_output=True)
    subprocess.run(["git", "-C", str(repo_path), "checkout", "--", "."], capture_output=True)
    current = git_get_branch(repo_path)
    if current != original_branch:
        subprocess.run(
            ["git", "-C", str(repo_path), "checkout", original_branch],
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "branch", "-D", current],
            capture_output=True,
        )


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


def _resolve_repo_path(rel: str, repo_path: Path) -> tuple[Path, str] | tuple[None, str]:
    """Return (resolved_path, error_message). error_message is empty on success."""
    rel = rel.lstrip("/\\")
    target = (repo_path / rel).resolve()
    try:
        target.relative_to(repo_path.resolve())
    except ValueError:
        return None, "Error: path escapes the repository root."
    return target, ""


def _execute_tool(name: str, tool_input: dict, repo_path: Path) -> str:
    if name == "read_file":
        rel = tool_input.get("path", "")
        target, err = _resolve_repo_path(rel, repo_path)
        if err:
            return err
        if not target.exists():
            return f"File not found: {rel}"
        if not target.is_file():
            return f"Not a file: {rel}"
        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"Binary file, cannot read as text: {rel}"
        except OSError as exc:
            return f"Error reading {rel}: {exc}"
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + f"\n\n[truncated — {len(content)} total chars]"
        return content

    if name == "write_file":
        rel = tool_input.get("path", "")
        content = tool_input.get("content", "")
        target, err = _resolve_repo_path(rel, repo_path)
        if err:
            return err
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return f"Error writing {rel}: {exc}"
        return f"Written: {rel} ({len(content)} chars)"

    return f"Unknown tool: {name}"


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


def call_agent_agentic(
    client: anthropic.Anthropic,
    mra_content: str,
    agent_content: str,
    user_message: str,
    repo_path: Path | None = None,
    write_enabled: bool = False,
) -> str:
    system = [
        {"type": "text", "text": mra_content, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": agent_content},
    ]
    messages: list[dict] = [{"role": "user", "content": user_message}]
    has_repo = repo_path and repo_path.exists()
    if has_repo and write_enabled:
        tools = [READ_FILE_TOOL, WRITE_FILE_TOOL]
    elif has_repo:
        tools = [READ_FILE_TOOL]
    else:
        tools = []
    tool_calls = 0

    while True:
        # Stop offering tools once the cap is reached so the model must finish.
        active_tools = tools if tool_calls < MAX_TOOL_CALLS else []
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            **({"tools": active_tools} if active_tools else {}),
        )

        if response.stop_reason != "tool_use":
            return "\n".join(b.text for b in response.content if hasattr(b, "text"))

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls += 1
                result = _execute_tool(block.name, block.input, repo_path)
                verb = "write" if block.name == "write_file" else "read"
                print(f"    [{verb}] {block.input.get('path', '?')}")
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": results})


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
    diff: str = "",
    branch: str = "",
) -> Path:
    out_dir = RUNS_DIR / project_name / ticket_stem
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "planner_output.md").write_text(planner_output, encoding="utf-8")
    (out_dir / "dev_output.md").write_text(dev_output, encoding="utf-8")
    (out_dir / "review_output.md").write_text(review_output, encoding="utf-8")
    if diff:
        (out_dir / "diff.patch").write_text(diff, encoding="utf-8")
    meta = {
        "run_id": run_id,
        "status": "pending",
        "approved": 0,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "branch": branch,
        "has_diff": bool(diff),
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
    original_branch = None
    work_branch = None
    has_repo = repo_path and repo_path.exists()

    if has_repo:
        print("Pulling latest changes ...")
        git_pull(repo_path)
        original_branch = git_get_branch(repo_path)
        work_branch = f"run/{ticket_path.stem}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        git_create_branch(repo_path, work_branch)
        print(f"  branch: {work_branch}")
        tree = build_tree(repo_path)
        tree_section = f"\n\n## Codebase structure\n\n```\n{repo_path.name}/\n{tree}\n```"
        print()

    print("[1/3] Planner (agentic) ...")
    planner_prompt = (
        f"## Project brief\n\n{brief_content}\n\n"
        f"## GitHub repo\n\n{github_url}"
        + (f"\n\n## Description\n\n{description}" if description else "")
        + tree_section
        + f"\n\n## Ticket\n\n{ticket_content}"
    )
    planner_output = call_agent_agentic(client, mra, planner_agent, planner_prompt, repo_path)

    print("[2/3] Developer (agentic, write enabled) ...")
    dev_prompt = (
        f"## Ticket\n\n{ticket_content}\n\n"
        f"## Planning notes\n\n{planner_output}"
    )
    dev_output = call_agent_agentic(
        client, mra, dev_agent, dev_prompt, repo_path, write_enabled=True
    )

    # Stage all dev writes and capture the diff for the Reviewer.
    diff_text = ""
    if has_repo:
        diff_text = git_stage_and_diff(repo_path)
        if diff_text:
            lines_changed = diff_text.count("\n")
            print(f"  {lines_changed} diff lines staged")
        else:
            print("  no file changes staged")

    print("[3/3] Reviewer ...")
    reviewer_prompt = (
        f"## Ticket (alignment reference)\n\n{ticket_content}\n\n"
        f"## Developer output\n\n{dev_output}"
    )
    if diff_text:
        reviewer_prompt += f"\n\n## Git diff (actual files changed)\n\n```diff\n{diff_text}\n```"
    review_output = call_agent(client, mra, reviewer_agent, reviewer_prompt)

    ticket_id = get_or_create_ticket(conn, project_id, ticket_path.name, ticket_path.stem)
    run_id = save_run(conn, ticket_id, dev_output, review_output)

    verdict = extract_verdict(review_output)

    # Commit approved changes; revert and clean up the branch on rejection.
    committed_branch = ""
    if has_repo:
        if verdict == "Approve" and diff_text:
            git_commit_run(repo_path, ticket_path.stem, run_id)
            committed_branch = work_branch
            print(f"  committed to branch: {work_branch}")
        else:
            git_revert_and_restore(repo_path, original_branch)
            work_branch = None
            print("  changes reverted — back on", original_branch)

    out_dir = write_outputs(
        args.project, ticket_path.stem, planner_output, dev_output, review_output, run_id,
        diff=diff_text, branch=committed_branch,
    )

    append_feedback_log(args.project, ticket_path.stem, run_id, verdict)

    conn.execute("UPDATE tickets SET status = 'done' WHERE id = ?", (ticket_id,))
    conn.commit()
    conn.close()

    print(f"\n{'─' * 50}")
    print(f"Run #{run_id} saved → {out_dir.relative_to(ROOT)}")
    print(f"Verdict : {verdict}")
    if committed_branch:
        print(f"Branch  : {committed_branch}")
    print(f"{'─' * 50}")


if __name__ == "__main__":
    main()
