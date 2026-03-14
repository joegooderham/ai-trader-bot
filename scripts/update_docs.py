"""
scripts/update_docs.py — Automated Documentation Updater
─────────────────────────────────────────────────────────
Runs after each push to main. Uses Claude API to analyse the git diff
and update any documentation that's gone stale:

  - Directory READMEs (only for directories with changed files)
  - Wiki pages (clones wiki repo, updates relevant pages, pushes)
  - CLAUDE.md (if architecture or key modules changed)

Designed to be called from GitHub Actions (update-docs.yml).
Skips gracefully if there's nothing meaningful to update.
"""

import os
import sys
import json
import subprocess
import re
from pathlib import Path
from anthropic import Anthropic

# ── Configuration ────────────────────────────────────────────────────────────

# Claude model for doc generation — fast + cheap, good enough for docs
CLAUDE_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096

# Directories that have READMEs to maintain
README_DIRS = [
    "bot", "bot/engine", "bot/engine/lstm", "bot/analytics",
    "broker", "data", "notifications", "risk",
    "mcp_server", "scripts", "config", "docs",
]

# Map of which code directories affect which wiki pages
# Key = wiki page filename (without .md), Value = list of directories that affect it
WIKI_PAGE_MAP = {
    "Architecture": ["bot", "docker-compose.yml", "Dockerfile", "Dockerfile.mcp"],
    "Trading-Logic": ["bot/engine", "risk", "bot/eod_manager.py", "bot/scheduler.py"],
    "LSTM-Engine": ["bot/engine/lstm", "bot/analytics"],
    "Configuration-Guide": ["bot/config.py", "config"],
    "Telegram-Commands": ["notifications"],
    "IG-Integration": ["broker"],
    "Analytics-and-Monitoring": ["bot/analytics", "bot/engine/lstm/drift.py", "mcp_server", "scripts/health_monitor.py"],
    "Deployment": [".github/workflows", "docker-compose.yml", "Dockerfile", "Dockerfile.mcp"],
    "Backlog-and-Roadmap": [],  # Updated manually or from issues
}

# Files/patterns that indicate CLAUDE.md might need updating
CLAUDE_MD_TRIGGERS = [
    "bot/config.py", "bot/scheduler.py", "docker-compose.yml",
    "bot/engine/", "mcp_server/server.py", "requirements.txt",
    "notifications/telegram_bot.py", "notifications/telegram_chat.py",
    "broker/ig_client.py", "data/storage.py",
]

REPO_ROOT = Path(__file__).parent.parent


def run_git(args: list[str], cwd: str = None) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True, text=True,
        cwd=cwd or str(REPO_ROOT),
    )
    return result.stdout.strip()


def get_diff_since_last_doc_update() -> tuple[str, list[str]]:
    """Get the git diff and list of changed files since the last doc-update commit.
    Falls back to HEAD~1 if no doc-update commit found."""

    # Look for the last commit made by this script
    log = run_git(["log", "--oneline", "--all", "-50"])
    last_doc_sha = None
    for line in log.splitlines():
        if "[docs-bot]" in line:
            last_doc_sha = line.split()[0]
            break

    # Determine the comparison base
    if last_doc_sha:
        base = last_doc_sha
    else:
        # First run — just compare against the previous commit
        base = "HEAD~1"

    # Get list of changed files
    changed_files_str = run_git(["diff", "--name-only", base, "HEAD"])
    changed_files = [f for f in changed_files_str.splitlines() if f.strip()]

    # Get the actual diff (limited to non-doc files for context)
    code_files = [f for f in changed_files if not f.endswith(".md")]
    if not code_files:
        return "", changed_files

    # Get diff but limit size to avoid token explosion
    diff = run_git(["diff", base, "HEAD", "--stat"] + ["--", *code_files[:20]])

    # Also get a summary diff (not full patch, just enough for Claude to understand)
    summary_diff = run_git(["diff", "--stat", base, "HEAD"])

    # Get commit messages for context
    commits = run_git(["log", "--oneline", f"{base}..HEAD"])

    return f"COMMITS:\n{commits}\n\nCHANGED FILES:\n{summary_diff}", changed_files


def get_directory_contents(directory: str) -> str:
    """List Python files in a directory with their first docstring line."""
    dir_path = REPO_ROOT / directory
    if not dir_path.exists():
        return f"Directory {directory} does not exist"

    contents = []
    for f in sorted(dir_path.iterdir()):
        if f.is_file() and f.suffix == ".py":
            # Read first few lines to get module docstring
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
                # Extract first docstring
                doc_match = re.search(r'"""(.*?)"""', text, re.DOTALL)
                first_line = doc_match.group(1).strip().split("\n")[0] if doc_match else ""
                contents.append(f"  - {f.name}: {first_line}")
            except Exception:
                contents.append(f"  - {f.name}")
        elif f.is_dir() and (f / "__init__.py").exists():
            contents.append(f"  - {f.name}/ (Python package)")

    return "\n".join(contents) if contents else "  (no Python files)"


def ask_claude(prompt: str, system: str = "") -> str:
    """Send a prompt to Claude and return the response text."""
    client = Anthropic()
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=system or "You are a technical documentation writer. Be concise and accurate. Output ONLY the requested content with no preamble or explanation.",
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def update_readme(directory: str, changed_files: list[str], diff_summary: str) -> bool:
    """Update a directory's README.md if files in that directory changed."""

    # Check if any changed files are in this directory
    dir_changes = [f for f in changed_files if f.startswith(directory + "/")]
    if not dir_changes:
        return False

    readme_path = REPO_ROOT / directory / "README.md"
    current_readme = ""
    if readme_path.exists():
        current_readme = readme_path.read_text(encoding="utf-8", errors="replace")

    # Get current directory contents
    dir_contents = get_directory_contents(directory)

    prompt = f"""Update the README.md for the `{directory}/` directory.

CURRENT DIRECTORY CONTENTS:
{dir_contents}

FILES CHANGED IN THIS DIRECTORY:
{chr(10).join(dir_changes)}

CURRENT README.md:
{current_readme}

RECENT CHANGES CONTEXT:
{diff_summary}

Write an updated README.md that:
1. Has a clear title and one-line description
2. Lists all Python files with brief descriptions of what they do
3. Notes any sub-packages/directories
4. Mentions key classes or entry points
5. Is concise — this is a high-level overview, not API docs
6. Preserves any still-accurate content from the current README
7. Does NOT include generic boilerplate like "Contributing" or "License"

Output ONLY the markdown content for the README.md file, nothing else."""

    new_readme = ask_claude(prompt)

    if new_readme.strip() == current_readme.strip():
        return False

    readme_path.write_text(new_readme, encoding="utf-8")
    print(f"  ✓ Updated {directory}/README.md")
    return True


def update_wiki_page(page_name: str, trigger_dirs: list[str],
                     changed_files: list[str], diff_summary: str,
                     wiki_dir: str) -> bool:
    """Update a wiki page if relevant code changed."""

    # Check if any changed files match the trigger directories
    relevant = False
    for trigger in trigger_dirs:
        for changed in changed_files:
            if changed.startswith(trigger) or changed == trigger:
                relevant = True
                break
        if relevant:
            break

    if not relevant:
        return False

    wiki_path = Path(wiki_dir) / f"{page_name}.md"
    current_content = ""
    if wiki_path.exists():
        current_content = wiki_path.read_text(encoding="utf-8", errors="replace")

    if not current_content:
        print(f"  ⚠ Wiki page {page_name} not found, skipping")
        return False

    # Gather relevant source file summaries
    relevant_sources = []
    for trigger in trigger_dirs:
        trigger_path = REPO_ROOT / trigger
        if trigger_path.is_dir():
            relevant_sources.append(f"\n{trigger}/:\n{get_directory_contents(trigger)}")
        elif trigger_path.is_file():
            try:
                text = trigger_path.read_text(encoding="utf-8", errors="replace")
                # Just first 50 lines for context
                lines = text.split("\n")[:50]
                relevant_sources.append(f"\n{trigger} (first 50 lines):\n" + "\n".join(lines))
            except Exception:
                pass

    prompt = f"""Update this wiki page based on recent code changes.

WIKI PAGE: {page_name}

CURRENT CONTENT:
{current_content}

RECENT CHANGES:
{diff_summary}

RELEVANT SOURCE FILES:
{"".join(relevant_sources)}

Update the wiki page to reflect any changes. Rules:
1. Preserve the overall structure and style
2. Only modify sections that are affected by the changes
3. Keep it accurate — don't add features that aren't in the code
4. If nothing meaningful changed for this page, return the content UNCHANGED
5. Keep diagrams and tables if they're still accurate

Output ONLY the full markdown content for the wiki page, nothing else."""

    new_content = ask_claude(prompt)

    if new_content.strip() == current_content.strip():
        return False

    wiki_path.write_text(new_content, encoding="utf-8")
    print(f"  ✓ Updated wiki: {page_name}")
    return True


def update_claude_md(changed_files: list[str], diff_summary: str) -> bool:
    """Update CLAUDE.md if significant architectural changes detected."""

    # Check if any trigger files were changed
    triggered = False
    for trigger in CLAUDE_MD_TRIGGERS:
        for changed in changed_files:
            if changed.startswith(trigger) or changed == trigger:
                triggered = True
                break
        if triggered:
            break

    if not triggered:
        return False

    claude_md_path = REPO_ROOT / "CLAUDE.md"
    current_content = claude_md_path.read_text(encoding="utf-8", errors="replace")

    prompt = f"""Review this CLAUDE.md file and update it based on recent code changes.

CURRENT CLAUDE.md:
{current_content}

RECENT CHANGES:
{diff_summary}

CHANGED FILES:
{chr(10).join(changed_files)}

Update CLAUDE.md to reflect the changes. Rules:
1. This file guides Claude Code (AI coding assistant) working in this repo
2. Only update sections affected by the changes
3. Keep build commands, git workflow rules, and constraints sections unless they changed
4. Focus on architecture, key modules table, and feature descriptions
5. Do NOT add generic development advice
6. If nothing meaningful changed, return the content UNCHANGED
7. Always start with the exact header: "# CLAUDE.md\\n\\nThis file provides guidance to Claude Code (claude.ai/code) when working with code in this repository."

Output ONLY the full markdown content for CLAUDE.md, nothing else."""

    new_content = ask_claude(prompt)

    if new_content.strip() == current_content.strip():
        return False

    claude_md_path.write_text(new_content, encoding="utf-8")
    print(f"  ✓ Updated CLAUDE.md")
    return True


def main():
    print("📝 AI Documentation Updater")
    print("═" * 40)

    # Check for API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("❌ ANTHROPIC_API_KEY not set, skipping doc update")
        sys.exit(0)

    # Get changes since last doc update
    print("\n📊 Analysing changes...")
    diff_summary, changed_files = get_diff_since_last_doc_update()

    if not changed_files:
        print("  No changes detected, nothing to update.")
        sys.exit(0)

    # Filter out markdown-only changes to avoid infinite loops
    code_changes = [f for f in changed_files if not f.endswith(".md")]
    if not code_changes:
        print("  Only markdown files changed, skipping to avoid loop.")
        sys.exit(0)

    print(f"  Found {len(code_changes)} code file(s) changed")

    any_updated = False

    # 1. Update directory READMEs
    print("\n📁 Checking directory READMEs...")
    for directory in README_DIRS:
        try:
            if update_readme(directory, changed_files, diff_summary):
                any_updated = True
        except Exception as e:
            print(f"  ⚠ Error updating {directory}/README.md: {e}")

    # 2. Update wiki pages
    print("\n📖 Checking wiki pages...")
    wiki_dir = "/tmp/ai-trader-bot-wiki"
    wiki_cloned = False

    # Check if any wiki pages need updating before cloning
    wiki_pages_to_update = []
    for page_name, triggers in WIKI_PAGE_MAP.items():
        if not triggers:
            continue
        for trigger in triggers:
            if any(f.startswith(trigger) or f == trigger for f in changed_files):
                wiki_pages_to_update.append(page_name)
                break

    if wiki_pages_to_update:
        # Clone wiki repo
        try:
            subprocess.run(
                ["git", "clone", "https://github.com/joegooderham/ai-trader-bot.wiki.git", wiki_dir],
                capture_output=True, text=True, check=True,
            )
            wiki_cloned = True
        except subprocess.CalledProcessError as e:
            print(f"  ⚠ Could not clone wiki: {e.stderr}")

    if wiki_cloned:
        for page_name in wiki_pages_to_update:
            try:
                if update_wiki_page(page_name, WIKI_PAGE_MAP[page_name],
                                    changed_files, diff_summary, wiki_dir):
                    any_updated = True
            except Exception as e:
                print(f"  ⚠ Error updating wiki {page_name}: {e}")

        # Push wiki changes if any
        wiki_status = run_git(["status", "--porcelain"], cwd=wiki_dir)
        if wiki_status:
            run_git(["add", "-A"], cwd=wiki_dir)
            run_git(["commit", "-m", "[docs-bot] Auto-update wiki from code changes"], cwd=wiki_dir)
            result = subprocess.run(
                ["git", "push"],
                capture_output=True, text=True, cwd=wiki_dir,
            )
            if result.returncode == 0:
                print("  ✓ Wiki changes pushed")
            else:
                print(f"  ⚠ Wiki push failed: {result.stderr}")
    else:
        print("  No wiki pages need updating")

    # 3. Update CLAUDE.md
    print("\n📋 Checking CLAUDE.md...")
    try:
        if update_claude_md(changed_files, diff_summary):
            any_updated = True
    except Exception as e:
        print(f"  ⚠ Error updating CLAUDE.md: {e}")

    # Summary
    print("\n" + "═" * 40)
    if any_updated:
        print("✅ Documentation updated successfully")
    else:
        print("ℹ️  No documentation changes needed")

    return any_updated


if __name__ == "__main__":
    updated = main()
    # Exit code 0 = success (even if nothing updated)
    # The GitHub Action workflow checks for actual file changes
    sys.exit(0)
