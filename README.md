# ASDA — Autonomous Sales Development Agent

A self-hostable sales-workspace for Altisec: ingest → scope and research leads → draft multi-channel outreach → review → run approved sequences. No n8n. Talk to it; paste credentials in the desk instead of committing them.

```
Ingestion (CSV / Apollo / webhook)
        ↓
Research (web-backed, tailored to this person)
        ↓
Content (playbook + role/company-aware drafts)
        ↓
Worker ── email / LinkedIn / WhatsApp sequence ── replies
        ↓
Learning loop (Sunday playbook) → next week's copy
```

This is a **hybrid agent**: LLM brain (research, copy, replies, talk, learn) and deterministic hands (sequence engine, SMTP/IMAP, PhantomBuster, Wappfly, APScheduler). Sending remains in practice mode until explicitly enabled.

## Quick start (anyone)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
asda ui
```

Open http://localhost:8501 and **talk**:

1. Paste an [OpenRouter](https://openrouter.ai/keys) key (`sk-or-v1-…`) — that's the brain
2. Work mailbox + Google App Password (or Outlook)
3. [PhantomBuster](https://phantombuster.com) API key
4. LinkedIn `li_at` cookie (Chrome → linkedin.com → DevTools → Application → Cookies)
5. Review drafts and set the required channels. Enable live sending only when the campaign is approved.

Keys land in `data/runtime.json` (gitignored). `.env` still works if you prefer files — see `.env.example`.

```bash
make test
docker compose up --build   # dashboard on :8501, worker included via the API process
```

**Nothing is sent until live sending is explicitly enabled.** Practice mode researches and writes; it does not contact people.

## Setup and channel guides

Start here:

- [Full local + Docker setup](docs/SETUP.md)
- [Email, LinkedIn, WhatsApp, Apollo, SignalHire, and reply-monitoring setup](docs/CHANNELS.md)
- [Campaign readiness checklist](docs/CAMPAIGN_READINESS.md)

Never commit `.env`, `data/`, database files, runtime configuration, exported lead lists, or passwords. They are already gitignored.

## What the desk shows

- **Now** — what the employee is doing, last tick, next jobs
- **Mail** — to send / on mail / conversations
- **LinkedIn** — invite sent / accepted / first message / conversations
- **This month** — outreach / replies / meetings vs target
- **Reports** — funnel, playbook, Monday brief

## Apollo

A Free key authenticates. **People Search / Match are locked on Free** (403). Organization search and CSV export work.

**Do not buy the ~$65 Basic plan unless you want ASDA to pull people from Apollo.** Upload an Apollo CSV on Leads instead. The rest of the agent (research, mail, LinkedIn, learning, reports) does not need Apollo.

## MCP (real, not dummy)

JSON-RPC 2.0, same tools as the desk.

Stdio (Claude Desktop / Cursor):

```json
{
  "mcpServers": {
    "asda": { "command": "asda", "args": ["mcp"] }
  }
}
```

HTTP: `POST /mcp` with a JSON-RPC body (`initialize`, `tools/list`, `tools/call`).  
REST: `GET /api/agent/tools` · `POST /api/agent/{tool}`.

Tools include `asda.status`, `asda.talk`, `asda.workboard`, `asda.validate`, `asda.leads.run`, `asda.worker.start|stop`, `asda.learn`.

## Recurring jobs (24/7)

| Job | Cadence |
|---|---|
| Due mail & LinkedIn steps | 5 min |
| Research + write new leads | 5 min |
| Read mailbox | 3 min |
| Read LinkedIn inbox | 15 min |
| CSV drop folder | 2 min |
| Playbook rewrite | Sunday 02:00 |
| CBO brief | Monday 08:00 |
| Numbers snapshot | 20:00 |

The API process restarts the worker if it dies. Start/stop from Home.

## Research & learning

Copy is required to open on a fact unique to **this** person. Generic lines are banned in the prompt. Sunday learning writes `data/playbook.json`; every new sequence is written against it.

## Configure the offer

Talk ("we are Altisec, ICP is enterprise security leaders in India") or edit `config/offer.yaml`. `config/safety.yaml` holds send caps.

LLM: OpenRouter by default (`anthropic/claude-sonnet-4` + `openai/gpt-4o-mini`). SpaceXAI / xAI also works.

## Commands

```bash
asda ui                 # dashboard :8501 + worker
asda ingest csv --path sample_data/leads.csv
asda run --limit 5 --skip-outreach
asda mcp                # stdio MCP server
asda learn
make test
```
