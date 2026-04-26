"""
import_github.py — import GitHub repos as projects into the database.

Requires a Personal Access Token:
  1. Go to https://github.com/settings/tokens → Generate new token (classic)
  2. Tick 'repo' scope (private repos) or no scope (public only)
  3. Copy the token, then:
       set GITHUB_TOKEN=ghp_...          (Windows CMD)
       $env:GITHUB_TOKEN="ghp_..."       (PowerShell)
       export GITHUB_TOKEN=ghp_...       (Mac/Linux)

Usage:
    python scripts/import_github.py                     # import everything
    python scripts/import_github.py --filter keyword    # only repos matching keyword
    python scripts/import_github.py --dry-run           # preview without writing
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "db" / "projects.db"
PROJECTS_DIR = ROOT / "projects"
REPOS_DIR = ROOT / "repos"
GITHUB_API = "https://api.github.com"


def github_get(url: str, token: str) -> list[dict] | dict:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise SystemExit(f"GitHub API error {e.code}: {body}") from e


def fetch_all_repos(token: str) -> list[dict]:
    repos: list[dict] = []
    page = 1
    while True:
        batch = github_get(
            f"{GITHUB_API}/user/repos?per_page=100&page={page}&sort=updated",
            token,
        )
        if not batch:
            break
        repos.extend(batch)
        page += 1
    return repos


def create_project_folder(name: str, github_url: str, description: str) -> bool:
    """Create projects/<name>/brief.md and projects/<name>/tickets/. Returns True if created."""
    project_dir = PROJECTS_DIR / name
    tickets_dir = project_dir / "tickets"
    if project_dir.exists():
        return False
    tickets_dir.mkdir(parents=True)
    brief = project_dir / "brief.md"
    brief.write_text(
        f"# {name}\n\n"
        f"**GitHub:** {github_url}\n\n"
        + (f"**Description:** {description}\n\n" if description else "")
        + "## What this project does\n\n"
        "<!-- Describe the project goal here. The Planner agent reads this. -->\n\n"
        "## Tech stack\n\n"
        "<!-- Language, frameworks, key libraries. -->\n\n"
        "## Notes\n\n"
        "<!-- Anything the agents should know before working on tickets. -->\n",
        encoding="utf-8",
    )
    return True


def clone_repo(name: str, clone_url: str, token: str) -> str | None:
    """Clone repo into repos/<name>/. Returns relative path string, or None on failure."""
    dest = REPOS_DIR / name
    if dest.exists():
        return str(dest.relative_to(ROOT))
    REPOS_DIR.mkdir(exist_ok=True)
    # Embed token so clone works without interactive prompt
    auth_url = clone_url.replace("https://", f"https://oauth2:{token}@")
    print(f"    Cloning into repos/{name} ...")
    result = subprocess.run(
        ["git", "clone", auth_url, str(dest)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"    git clone failed: {result.stderr.strip()}")
        return None
    return str(dest.relative_to(ROOT))


def register_project(
    conn: sqlite3.Connection,
    name: str,
    github_url: str,
    description: str | None,
) -> bool:
    try:
        conn.execute(
            "INSERT INTO projects (name, github_url, description) VALUES (?, ?, ?)",
            (name, github_url, description or None),
        )
        return True
    except sqlite3.IntegrityError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Import GitHub repos as projects.")
    parser.add_argument("--filter", metavar="TEXT", help="Only import repos whose name contains TEXT")
    parser.add_argument("--clone", action="store_true", help="Clone each repo into repos/<name>/")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to the database")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit(
            "GITHUB_TOKEN is not set.\n\n"
            "Create one at: https://github.com/settings/tokens\n"
            "Then set it:   set GITHUB_TOKEN=ghp_...\n"
        )

    if not DB_PATH.exists():
        raise SystemExit("Database not found. Run  python scripts/init_db.py  first.")

    print("Fetching repos from GitHub...")
    repos = fetch_all_repos(token)
    print(f"Found {len(repos)} repo(s) on your account.\n")

    if args.filter:
        repos = [r for r in repos if args.filter.lower() in r["name"].lower()]
        print(f"After filter '{args.filter}': {len(repos)} repo(s).\n")

    if not repos:
        print("Nothing to import.")
        return

    conn = sqlite3.connect(DB_PATH)
    imported = skipped = 0

    for repo in repos:
        name: str = repo["name"]
        url: str = repo["html_url"]
        desc: str = repo.get("description") or ""
        visibility: str = "private" if repo.get("private") else "public"

        clone_url: str = repo.get("clone_url") or url

        if args.dry_run:
            exists = conn.execute(
                "SELECT id FROM projects WHERE name = ?", (name,)
            ).fetchone()
            cloned = (ROOT / "repos" / name).exists()
            status = "already registered" if exists else visibility
            clone_status = "cloned" if cloned else ("will clone" if args.clone else "no clone")
            print(f"  {name}  [{status}] [{clone_status}]")
            print(f"    {url}")
            if desc:
                print(f"    {desc}")
        else:
            added = register_project(conn, name, url, desc)
            if added:
                imported += 1
                folder_created = create_project_folder(name, url, desc)
                folder_note = "folder created" if folder_created else "folder existed"
                repo_path: str | None = None
                if args.clone:
                    repo_path = clone_repo(name, clone_url, token)
                if repo_path:
                    conn.execute(
                        "UPDATE projects SET repo_path = ? WHERE name = ?",
                        (repo_path, name),
                    )
                print(f"  + {name}  ({visibility}, {folder_note}" + (", cloned" if repo_path else "") + ")")
            else:
                skipped += 1
                print(f"  · {name}  (already registered)")

    if not args.dry_run:
        conn.commit()
        print(f"\nDone: {imported} imported, {skipped} already existed.")

    conn.close()


if __name__ == "__main__":
    main()
