# scripts/

Standalone utility scripts that run as separate processes.

| File | Purpose |
|------|---------|
| `health_monitor.py` | Runs as its own Docker container. Checks bot, MCP server, IG API reachability, and disk space every 60 seconds. Sends Telegram alerts on failure and recovery notifications when issues resolve. |
