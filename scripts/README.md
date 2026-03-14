# scripts/

Standalone utility scripts that run as separate processes.

| File | Purpose |
|------|---------|
| `health_monitor.py` | Runs as its own Docker container. Checks bot, MCP server, IG API reachability, and disk space every 60 seconds. Sends Telegram alerts on failure and recovery notifications when issues resolve. |
| `update_docs.py` | Automated documentation updater. Runs via GitHub Actions after each code push to main. Uses Claude API to analyse the diff and update directory READMEs, wiki pages, and CLAUDE.md. |
