# MulteAgent — Team Multi-Agent + Agentic Platform

Local-first, framework-free multi-agent system with:

- **Auto** intent router (default) — greetings/stories → chat; decisions → debate; research → agentic
- **Chat** — single fast Assistant reply
- **Debate** (Optimist / Cynic / Consensus)
- **Agentic** planner → tools → critic loop
- **Mixed** (agentic research, then debate)
- **Vision** — paste/attach images; Auto routes to `MODEL_VISION` (default `llava:7b`)
- **Job queue**, JWT auth, model registry, GPU fairness for 8–15 users
- Hybrid path: SQLite/LAN now → Postgres + Caddy + workers for cloud

## Quick start (LAN team)

```bash
cd D:\multeagent
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env

# Terminal 1 — API + in-process worker
python main.py --gui

# Terminal 2 — UI
cd frontend
npm install
npm run dev
```

Open the Next.js URL, sign in with `admin` / `admin123`.  
(Multi-user accounts are deferred — admin-only for testing right now.)

## Architecture (short)

| Piece | Role |
|-------|------|
| FastAPI (`web/server.py`) | Auth, agents, models, jobs, SSE |
| Intent router (`src/intent_router.py`) | Auto mode: chat vs debate vs agentic |
| Chat (`src/chat_runner.py`) | Single-call Assistant replies |
| Worker (`src/worker.py` / `worker_main.py`) | Claims jobs, runs chat/debate/agentic |
| DB (SQLite or Postgres) | Users, jobs, events, model registry |
| LLM lock (`LLM_SLOTS`) | Fair GPU concurrency |
| Next.js (`frontend/`) | Team UI |

**Full review doc (what / how / why / libraries):** [FULL_PROJECT_PLAN.md](FULL_PROJECT_PLAN.md)

Also see [multi_agent_project_plan.md](multi_agent_project_plan.md).

## Horizontal workers

```bash
# Same machine / VMs sharing DATABASE_URL
python main.py --worker
```

Or:

```bash
docker compose up --build --scale worker=3
```

## Postgres (team / cloud)

```bash
docker compose up -d db
# set DATABASE_URL=postgresql+psycopg://multeagent:multeagent@localhost:5432/multeagent
python main.py --gui
```

TLS / reverse proxy starter: [deploy/Caddyfile](deploy/Caddyfile). SSO is gated by `SSO_ENABLED` (hook endpoint `/api/auth/sso/status`).

## CLI (no UI)

```bash
python main.py "Should we adopt AI route planning in Q3?"
python main.py --parallel "Your question"
```

## Defaults

- Admin: `admin` / `admin123` — change immediately
- `LLM_SLOTS=1` for 8GB GPUs
- Model roles: `MODEL_FAST`, `MODEL_STRONG`, optional `MODEL_CLOUD`
- Vision: `MODEL_VISION=llava:7b` — used whenever a message has images

## Chat UI

- **Enter** sends; **Shift+Enter** inserts a new line
- Paperclip icon attaches images; compact send icon submits
- **Edit** on a user message loads it into the composer so you can change it and send again
- **Retry** on the last turn regenerates without duplicating the user bubble
- Paste or drop images (up to 4, 5MB each)
