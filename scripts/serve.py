"""
serve.py — Flask web server for the devops-loop UI.

Usage:
    python scripts/serve.py

Opens on http://localhost:5000 (desktop) or http://<YOUR_IP>:5000 (phone).
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import anthropic

from flask import Flask, Response, jsonify, request, send_from_directory

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "db" / "projects.db"
FEEDBACK_LOG = ROOT / "feedback_log.md"
WEB_DIR = ROOT / "web"
PROJECTS_DIR = ROOT / "projects"
REPOS_DIR = ROOT / "repos"

TREE_EXCLUDE = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", ".tox",
    "dist", "build", ".eggs",
}

app = Flask(__name__, static_folder=str(WEB_DIR))

_running: dict[str, bool] = {}


def _run_in_background(project_name: str, ticket_number: str) -> None:
    key = f"{project_name}:{ticket_number}"
    _running[key] = True
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run.py"),
             "--project", project_name, "--ticket", ticket_number],
            cwd=str(ROOT),
        )
    finally:
        _running.pop(key, None)


SPAWN_MODEL = "claude-haiku-4-5-20251001"
SPAWN_PROMPT = """\
A ticket was reviewed and flagged as needing work.

Original ticket: {ticket_filename}

Reviewer output:
{review_output}

Generate 1-3 follow-up tickets — one per distinct issue — so a developer can fix each problem separately.
Reply with ONLY a valid JSON array, no markdown fences, no extra text:
[
  {{
    "title": "short imperative phrase, max 60 chars",
    "body": "## Goal\\nOne sentence.\\n\\n## Acceptance criteria\\n- [ ] specific checkable item"
  }}
]"""


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _next_ticket_number(tickets_dir: Path) -> str:
    numbers = [
        int(m.group(1))
        for p in tickets_dir.glob("*.md")
        if (m := re.match(r"^(\d+)-", p.name))
    ]
    return str(max(numbers, default=0) + 1).zfill(3)


def _create_ticket(
    conn: sqlite3.Connection,
    project_id: int,
    project_name: str,
    title: str,
    body: str,
) -> str:
    tickets_dir = PROJECTS_DIR / project_name / "tickets"
    tickets_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{_next_ticket_number(tickets_dir)}-{_slugify(title)}.md"
    (tickets_dir / filename).write_text(body, encoding="utf-8")
    conn.execute(
        "INSERT INTO tickets (project_id, filename, title, status) VALUES (?, ?, ?, 'open')",
        (project_id, filename, title),
    )
    conn.commit()
    return filename


def _spawn_followup_tickets(
    project_id: int,
    project_name: str,
    ticket_filename: str,
    review_output: str,
) -> list[str]:
    client = anthropic.Anthropic()
    prompt = SPAWN_PROMPT.format(
        ticket_filename=ticket_filename,
        review_output=review_output,
    )
    resp = client.messages.create(
        model=SPAWN_MODEL,
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    text = re.sub(r"^```[a-z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text.strip())
    items = json.loads(text)

    conn = get_db()
    created = []
    for item in items[:3]:
        filename = _create_ticket(
            conn, project_id, project_name,
            item["title"], item.get("body", f"## Goal\n{item['title']}\n")
        )
        created.append(filename)
    conn.close()
    return created


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


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Static pages ────────────────────────────────────────────────────────────

@app.route("/")
def index() -> Response:
    return send_from_directory(WEB_DIR, "index.html")


@app.route("/dashboard.html")
def dashboard_page() -> Response:
    return send_from_directory(WEB_DIR, "dashboard.html")


@app.route("/project.html")
def project_page() -> Response:
    return send_from_directory(WEB_DIR, "project.html")


@app.route("/style.css")
def style() -> Response:
    return send_from_directory(WEB_DIR, "style.css")


# ── API ─────────────────────────────────────────────────────────────────────

@app.route("/api/dashboard")
def dashboard_api() -> Response:
    conn = get_db()

    proj = conn.execute(
        "SELECT COUNT(*) AS total, SUM(status='active') AS active FROM projects"
    ).fetchone()

    ticket_rows = conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM tickets GROUP BY status"
    ).fetchall()
    tc = {r["status"]: r["cnt"] for r in ticket_rows}

    run_row = conn.execute("""
        SELECT
            COUNT(*)            AS total,
            SUM(approved =  1)  AS approved,
            SUM(approved = -1)  AS flagged,
            SUM(approved =  0)  AS pending
        FROM runs
    """).fetchone()

    recent = conn.execute("""
        SELECT r.id AS run_id, r.approved, r.created_at,
               t.filename AS ticket_filename,
               p.name AS project_name, p.id AS project_id
        FROM runs r
        JOIN tickets t ON t.id  = r.ticket_id
        JOIN projects p ON p.id = t.project_id
        ORDER BY r.created_at DESC
        LIMIT 15
    """).fetchall()
    conn.close()

    currently_running = []
    for key, active in _running.items():
        if not active:
            continue
        project_name, ticket_number = key.split(":", 1)
        tickets_dir = PROJECTS_DIR / project_name / "tickets"
        matches = list(tickets_dir.glob(f"{ticket_number}-*.md")) if tickets_dir.exists() else []
        filename = matches[0].name if matches else f"{ticket_number}-?.md"
        currently_running.append({"project": project_name, "filename": filename})

    return jsonify({
        "projects": {"total": proj["total"] or 0, "active": proj["active"] or 0},
        "tickets":  {"open": tc.get("open", 0), "running": tc.get("running", 0), "done": tc.get("done", 0)},
        "runs":     {
            "total":    run_row["total"]    or 0,
            "approved": run_row["approved"] or 0,
            "flagged":  run_row["flagged"]  or 0,
            "pending":  run_row["pending"]  or 0,
        },
        "currently_running": currently_running,
        "recent_runs": [dict(r) for r in recent],
    })


@app.route("/api/projects", methods=["POST"])
def create_project() -> Response:
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    github_url = data.get("github_url", "").strip()
    description = data.get("description", "").strip() or None
    repo_path_str = data.get("repo_path", "").strip() or None

    if not name or not github_url:
        return jsonify({"error": "name and github_url are required"}), 400

    conn = get_db()
    try:
        cursor = conn.execute(
            "INSERT INTO projects (name, github_url, repo_path, description) VALUES (?, ?, ?, ?)",
            (name, github_url, repo_path_str, description),
        )
        project_id = cursor.lastrowid
        conn.commit()
        conn.close()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": f"Project '{name}' already exists"}), 409

    project_dir = PROJECTS_DIR / name
    (project_dir / "tickets").mkdir(parents=True, exist_ok=True)
    brief_path = project_dir / "brief.md"
    if not brief_path.exists():
        brief_path.write_text(f"# {name}\n\nDescribe this project here.\n", encoding="utf-8")

    return jsonify({"id": project_id, "name": name}), 201


@app.route("/api/projects/<int:project_id>", methods=["DELETE"])
def delete_project(project_id: int) -> Response:
    conn = get_db()
    project = conn.execute("SELECT name FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return jsonify({"error": "not found"}), 404

    conn.execute("""
        DELETE FROM feedback WHERE run_id IN (
            SELECT r.id FROM runs r
            JOIN tickets t ON t.id = r.ticket_id
            WHERE t.project_id = ?
        )
    """, (project_id,))
    conn.execute("""
        DELETE FROM runs WHERE ticket_id IN (
            SELECT id FROM tickets WHERE project_id = ?
        )
    """, (project_id,))
    conn.execute("DELETE FROM tickets WHERE project_id = ?", (project_id,))
    conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    conn.close()
    return jsonify({"deleted": project_id})


@app.route("/api/projects")
def list_projects() -> Response:
    conn = get_db()
    rows = conn.execute("""
        SELECT
            p.id,
            p.name,
            p.github_url,
            p.description,
            p.status,
            p.created_at,
            COUNT(DISTINCT t.id)  AS ticket_count,
            MAX(r.created_at)     AS last_run_at
        FROM projects p
        LEFT JOIN tickets t ON t.project_id = p.id
        LEFT JOIN runs r    ON r.ticket_id  = t.id
        GROUP BY p.id
        ORDER BY p.id
    """).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/projects/<int:project_id>/runs")
def project_runs(project_id: int) -> Response:
    conn = get_db()
    project = conn.execute(
        "SELECT id, name, github_url, description, status, repo_path FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if not project:
        conn.close()
        return jsonify({"error": "project not found"}), 404

    name = project["name"]

    # Brief
    brief_path = PROJECTS_DIR / name / "brief.md"
    brief = brief_path.read_text(encoding="utf-8") if brief_path.exists() else None

    # Tickets from filesystem joined with DB status
    tickets_dir = PROJECTS_DIR / name / "tickets"
    db_tickets = {
        row["filename"]: dict(row)
        for row in conn.execute(
            "SELECT filename, status FROM tickets WHERE project_id = ?", (project_id,)
        ).fetchall()
    }
    tickets = []
    if tickets_dir.exists():
        for tf in sorted(tickets_dir.glob("*.md")):
            m = re.match(r"^(\d+)-", tf.name)
            number = m.group(1) if m else ""
            tickets.append({
                "filename": tf.name,
                "content": tf.read_text(encoding="utf-8"),
                "status": db_tickets.get(tf.name, {}).get("status", "open"),
                "is_running": bool(_running.get(f"{name}:{number}")),
            })

    # Codebase tree
    tree = None
    repo_path_str = project["repo_path"]
    if repo_path_str:
        repo_path = ROOT / repo_path_str
        if repo_path.exists():
            tree = f"{repo_path.name}/\n{build_tree(repo_path)}"

    # Runs
    runs = conn.execute("""
        SELECT
            r.id          AS run_id,
            r.approved,
            r.created_at,
            r.review_output,
            r.dev_output,
            t.filename    AS ticket_filename,
            t.title       AS ticket_title,
            t.status      AS ticket_status
        FROM runs r
        JOIN tickets t ON t.id = r.ticket_id
        WHERE t.project_id = ?
        ORDER BY r.created_at DESC
    """, (project_id,)).fetchall()
    conn.close()

    runs_out = []
    for r in runs:
        rd = dict(r)
        stem = Path(r["ticket_filename"]).stem
        run_dir = ROOT / "runs" / name / stem
        planner_path = run_dir / "planner_output.md"
        diff_path = run_dir / "diff.patch"
        meta_path = run_dir / "meta.json"
        rd["planner_output"] = planner_path.read_text(encoding="utf-8") if planner_path.exists() else None
        rd["diff"] = diff_path.read_text(encoding="utf-8") if diff_path.exists() else None
        rd["branch"] = None
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                rd["branch"] = meta.get("branch") or None
            except (json.JSONDecodeError, OSError):
                pass
        runs_out.append(rd)

    return jsonify({
        "project": dict(project),
        "brief": brief,
        "tickets": tickets,
        "tree": tree,
        "runs": runs_out,
    })


@app.route("/api/runs/<int:run_id>/approve", methods=["POST"])
def approve_run(run_id: int) -> Response:
    note = (request.get_json(silent=True) or {}).get("note", "")
    return _set_verdict(run_id, approved=1, note=note, label="approved")


@app.route("/api/runs/<int:run_id>/flag", methods=["POST"])
def flag_run(run_id: int) -> Response:
    note = (request.get_json(silent=True) or {}).get("note", "")

    conn = get_db()
    ctx = conn.execute("""
        SELECT r.review_output, t.filename, t.project_id, p.name AS project_name
        FROM runs r
        JOIN tickets t ON t.id  = r.ticket_id
        JOIN projects p ON p.id = t.project_id
        WHERE r.id = ?
    """, (run_id,)).fetchone()
    conn.close()

    resp = _set_verdict(run_id, approved=-1, note=note, label="flagged")
    if resp.status_code != 200 or not ctx:
        return resp

    spawned: list[str] = []
    try:
        spawned = _spawn_followup_tickets(
            ctx["project_id"], ctx["project_name"],
            ctx["filename"], ctx["review_output"] or "",
        )
    except Exception as exc:
        app.logger.warning("spawn_followup_tickets failed: %s", exc)

    body = resp.get_json()
    body["spawned_tickets"] = spawned
    return jsonify(body)


@app.route("/api/projects/<int:project_id>/tickets", methods=["POST"])
def create_ticket_api(project_id: int) -> Response:
    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    body = data.get("body", "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400

    conn = get_db()
    project = conn.execute("SELECT name FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return jsonify({"error": "not found"}), 404

    if not body:
        body = f"## Goal\n{title}\n\n## Acceptance criteria\n- [ ]\n"

    filename = _create_ticket(conn, project_id, project["name"], title, body)
    conn.close()
    ticket_path = PROJECTS_DIR / project["name"] / "tickets" / filename
    return jsonify({
        "filename": filename,
        "content": ticket_path.read_text(encoding="utf-8"),
        "status": "open",
        "is_running": False,
    }), 201


@app.route("/api/projects/<int:project_id>/tickets/<path:filename>", methods=["DELETE"])
def delete_ticket_api(project_id: int, filename: str) -> Response:
    conn = get_db()
    project = conn.execute("SELECT name FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not project:
        conn.close()
        return jsonify({"error": "not found"}), 404

    conn.execute(
        "DELETE FROM tickets WHERE project_id = ? AND filename = ?",
        (project_id, filename),
    )
    conn.commit()
    conn.close()

    ticket_path = PROJECTS_DIR / project["name"] / "tickets" / filename
    if ticket_path.exists():
        ticket_path.unlink()

    return jsonify({"deleted": filename})


@app.route("/api/projects/<int:project_id>/brief", methods=["PUT"])
def update_brief(project_id: int) -> Response:
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")

    conn = get_db()
    project = conn.execute("SELECT name FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    if not project:
        return jsonify({"error": "not found"}), 404

    (PROJECTS_DIR / project["name"] / "brief.md").write_text(content, encoding="utf-8")
    return jsonify({"saved": True})


@app.route("/api/projects/<int:project_id>/tickets/run", methods=["POST"])
def trigger_run(project_id: int) -> Response:
    body = request.get_json(silent=True) or {}
    filename = body.get("filename", "")
    if not filename:
        return jsonify({"error": "filename required"}), 400

    conn = get_db()
    project = conn.execute(
        "SELECT name FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    conn.close()
    if not project:
        return jsonify({"error": "project not found"}), 404

    m = re.match(r"^(\d+)-", filename)
    if not m:
        return jsonify({"error": "cannot parse ticket number from filename"}), 400

    ticket_number = m.group(1)
    project_name = project["name"]
    key = f"{project_name}:{ticket_number}"

    if _running.get(key):
        return jsonify({"error": "already running"}), 409

    t = threading.Thread(
        target=_run_in_background, args=(project_name, ticket_number), daemon=True
    )
    t.start()
    return jsonify({"status": "started"})


def _set_verdict(run_id: int, approved: int, note: str, label: str) -> Response:
    conn = get_db()
    row = conn.execute("SELECT id FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "run not found"}), 404

    conn.execute("UPDATE runs SET approved = ? WHERE id = ?", (approved, run_id))
    conn.execute(
        "INSERT INTO feedback (run_id, note) VALUES (?, ?)",
        (run_id, note or label),
    )
    conn.commit()
    conn.close()

    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    entry = f"[{timestamp}] run#{run_id} marked {label}" + (f" — {note}" if note else "") + "\n"
    with FEEDBACK_LOG.open("a", encoding="utf-8") as f:
        f.write(entry)

    return jsonify({"run_id": run_id, "status": label})


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DB_PATH.exists():
        raise SystemExit(
            "Database not found. Run first:\n  python scripts/init_db.py"
        )
    print(f"Starting server — http://localhost:5000")
    print(f"On your phone  — http://<your-local-ip>:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
