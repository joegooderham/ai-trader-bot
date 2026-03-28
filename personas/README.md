# Agent Personas

Structured instruction files for each AI agent role in the trading system.
Each persona defines the agent's role, expertise, inputs, constraints, output
format, and worked examples.

## Architecture

Based on the [TradingAgents](https://arxiv.org/abs/2412.20138) multi-agent
framework (ICAIF 2025), adapted for our forex bot. Follows Anthropic's
[Agent Skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
format with XML-tagged sections.

```
                  +---------------------+
                  |  Market Scan Start  |
                  +---------+-----------+
                            |
              +-------------+-------------+
              |             |             |
    +---------v--+  +-------v------+  +--v-----------+
    | Technical  |  |    LSTM      |  |   Market     |
    | Analyst    |  |  Predictor   |  |   Context    |
    +-----+------+  +------+-------+  +------+-------+
          |                |                  |
          +-------+--------+---------+--------+
                  |                  |
          +-------v--------+  +-----v--------+
          |     Trade      |  |    Trade     |
          |  Orchestrator  +->+    Critic    |
          +-------+--------+  +------+-------+
                  |                   |
                  +-------+-----------+
                          |
                  +-------v--------+
                  | Risk Manager   |
                  | (VETO power)   |
                  +-------+--------+
                          |
                  +-------v--------+
                  |   Execute or   |
                  |     Skip       |
                  +----------------+
```

## Personas

| File | Agent | Weight | Authority |
|------|-------|--------|-----------|
| [technical-analyst.md](technical-analyst.md) | Technical Analyst | 45% of confidence | Advisory |
| [lstm-predictor.md](lstm-predictor.md) | LSTM Predictor | 50% of confidence | Advisory |
| [market-context.md](market-context.md) | Market Context Analyst | -20 to +10 modifier | Advisory |
| [risk-manager.md](risk-manager.md) | Risk Manager | N/A | **VETO** |
| [trade-orchestrator.md](trade-orchestrator.md) | Trade Orchestrator | Synthesises all | **Decision** |
| [trade-critic.md](trade-critic.md) | Trade Critic | -15 to 0 modifier | Advisory |

## Decision Flow

1. **Parallel Analysis** — Technical Analyst, LSTM Predictor, and Market Context
   Agent all analyse the pair independently and simultaneously
2. **Synthesis** — Trade Orchestrator collects all outputs, resolves conflicts,
   calculates the weighted confidence score
3. **Critique** — Trade Critic stress-tests the proposal with "what could go wrong?"
4. **Risk Check** — Risk Manager validates position sizing, correlation, and portfolio
   limits. Has VETO authority regardless of confidence score.
5. **Execute or Skip** — if all gates pass, the trade is placed via IG API

## How Personas Are Used

These files serve three purposes:

1. **System prompts** — each persona's `<role>`, `<instructions>`, and `<constraints>`
   sections can be loaded as Claude system prompts when the agent is invoked
2. **Documentation** — the full persona file documents how each component of the
   trading system thinks and what its boundaries are
3. **Audit trail** — by logging which agents agreed/disagreed on each trade, the
   owner can review decision quality and identify which agent is miscalibrated

## Design Principles

- **Separation of concerns** — each agent has ONE job and clear boundaries
- **Explicit constraints** — every agent knows what it CANNOT do
- **Structured outputs** — JSON schemas so agents can consume each other's outputs
- **Worked examples** — each persona includes realistic examples with edge cases
- **Conservative by default** — the Risk Manager and Trade Critic are pessimists;
  the system is designed to miss trades rather than take bad ones

## Sources

- [TradingAgents Framework (arXiv:2412.20138)](https://arxiv.org/abs/2412.20138)
- [Anthropic: Building Effective AI Agents](https://resources.anthropic.com/building-effective-ai-agents)
- [Anthropic: Effective Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Claude Agent SDK — Subagents](https://platform.claude.com/docs/en/agent-sdk/subagents)
- [QuantAgent (arXiv:2509.09995)](https://arxiv.org/abs/2509.09995)
- [Two Sigma: AI in Investment Management 2026](https://www.twosigma.com/articles/ai-in-investment-management-2026-outlook-part-i/)
- [AgenticTrading (Open-Finance-Lab)](https://github.com/Open-Finance-Lab/AgenticTrading)
