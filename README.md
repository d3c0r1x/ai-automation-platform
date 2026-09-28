# AI Automation Platform

**Turn a natural-language task into a validated plan, execute the plan with tools, pause for human approval when needed, and return a structured result.**

[![CI](https://github.com/d3c0r1x/ai-automation-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/d3c0r1x/ai-automation-platform/actions/workflows/ci.yml)

## Example

> Find 20 items under 5000 ₽, compare them, select the best 5 and prepare a table.

Flow:

```
request
  ↓
planner
  ↓
validated plan
  ↓
queue
  ↓
worker
  ├─ API tools
  ├─ browser tools
  └─ deterministic calculations
  ↓
approval gate
  ↓
report
```

The repository includes a deterministic demo path, so it can be inspected and run without an LLM key.

## What is implemented

- FastAPI API and web dashboard
- typed tool contracts with Pydantic
- plan validation before execution
- Redis-backed worker queue
- persisted task state and resumable execution
- retries, timeouts and checkpoints
- SSE progress stream
- Playwright browser tool
- human approval before external/irreversible actions
- PostgreSQL and SQLite storage paths
- Docker Compose
- GitHub Actions
- 90+ deterministic/integration checks

## Architecture

```
Web / REST / Webhook
        ↓
      FastAPI
        ↓
 Queue (Redis)
        ↓
     Worker
        ↓
Planner → validation → executor
        ↓
tools / browser / calculations
        ↓
events + persisted state
        ↓
dashboard / report
```

## Engineering focus

**Plan as data.** A plan can be inspected, validated, persisted and resumed.

**Numbers in code.** Hard constraints and calculations are handled deterministically instead of delegated to the model.

**Approval at the boundary.** The system can stop before a message or other external side effect is sent.

**Deterministic fallback.** The portfolio demo works even without external services.

## Stack

Python 3.12 · FastAPI · Pydantic · PostgreSQL · SQLite · Redis · Playwright · React · TypeScript · Docker · GitHub Actions · pytest · ruff

## Local run

```bash
python -m app.demo
```

Full API + worker + dashboard setup is documented in [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Limitations

This is an MVP, not a production autonomous agent. The planner is intentionally conservative, browser content is not yet fully protected against prompt injection, and the dashboard has less behavioural test coverage than the backend.

## AI-assisted development

AI was used for implementation drafts, routine modules and test ideas.

I owned the task decomposition, architecture, tool contracts, integration behaviour, debugging, validation and final product behaviour.
