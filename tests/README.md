# tests/

Automated test suite. Run via Docker (no local Python installation required).

| File | Purpose |
|------|---------|
| `test_remediation.py` | 68 unit tests for the automated remediation system: smart losing streak analysis, direction performance detection, weekly P&L auto-pause, apply_action (all 6 types), hourly/deep review integration, inline button support, full end-to-end flows. |

## Running Tests

```bash
# Via Docker (recommended — no local dependencies needed)
docker run --rm -v "$(pwd):/app" -w /app python:3.11-slim \
  sh -c "pip install -q pyyaml loguru && python tests/test_remediation.py"

# Or with pytest if installed
python -m pytest tests/ -v
```

Tests mock all external dependencies (Telegram, IG broker, Anthropic) so they run without API keys or network access.
