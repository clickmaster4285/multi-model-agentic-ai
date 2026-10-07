# MulteAgent — Full Project Plan (Read & Review)

This is the single review document for the whole system: **what** we are building, **why**, **how**, **which tools/libraries**, and **why not** other choices.

**Locked decisions**

- Deploy: **Hybrid** — shared LAN server now, designed to move to cloud later
- Scale: **8–15 concurrent users**
- Agent frameworks in the core loop: **none** (no CrewAI / AutoGen / LangChain)
- **Users module: DEFERRED** — only default **admin / admin123** for testing; multi-user accounts later

---

## 1. One-sentence product

MulteAgent is a **team-usable, local-first platform** where people submit goals/questions as **jobs**; an **adaptive intent router** picks **chat**, **debate**, **agentic**, or **mixed** before heavy LLM work; the UI shows live progress.

---



## 2. Problems we are solving


| Problem                  | Why it matters                                | Our answer                               |
| ------------------------ | --------------------------------------------- | ---------------------------------------- |
| One GPU, many teammates  | 8GB VRAM dies if everyone hits Ollama at once | Job queue + global LLM slots             |
| Long runs in HTTP        | 1–3 minute debates break browsers/proxies     | Async jobs + SSE event stream            |
| Only “chat opinions”     | Real work needs plan → act → check            | Agentic runtime with allowlisted tools   |
| One model for everything | Queue backs up; small tasks waste big models  | Model registry + fast/strong/cloud roles |
| Every “hi” runs 3 agents | Wastes GPU time on greetings / stories        | Adaptive intent router (Auto → chat)     |
| No identity              | Cannot audit, quota, or prioritize            | JWT login + per-user job history         |
| Framework lock-in        | Opaque prompts, heavy deps, hard to debug     | Own thin Python orchestration            |


---



## 3. What “Agentic AI” means here

**Agentic AI** = systems that can **plan, reason, and execute multi-step work** toward a goal with limited supervision.


| Concept         | In MulteAgent                                      |
| --------------- | -------------------------------------------------- |
| Goal            | User query / objective                             |
| Plan            | Planner LLM returns JSON steps                     |
| Act             | Worker may call tools or write a step answer       |
| Observe         | Tool results fed back into next worker turn        |
| Critique        | Critic LLM scores completeness                     |
| Human oversight | User starts/cancels jobs; logs/artifacts auditable |


**Debate** is not fully agentic (personas answer once).  
**Agentic / Mixed** modes are.

---



## 4. Run modes (+ Auto router)


| Mode        | Flow                                      | Use when                           |
| ----------- | ----------------------------------------- | ---------------------------------- |
| **Auto**    | Intent router → chat / debate / agentic   | Default UI mode                    |
| **Chat**    | Single Assistant LLM call                 | Greetings, stories, simple Q&A     |
| **Debate**  | Panel agents → Consensus                  | Decisions, risk review             |
| **Agentic** | Planner → Worker(+tools) → Critic         | Research, drafts, multi-step goals |
| **Mixed**   | Agentic first, then Debate on the summary | Best for serious “should we…” work |


```text
User submits job (mode=auto|chat|debate|agentic|mixed)
    │
    ├─ auto ──► intent_router.classify_query
    │              ├─ chat ─────► single Assistant reply
    │              ├─ debate ───► panel → consensus
    │              └─ agentic ──► plan → tools/steps → critic
    ├─ chat ────► chat_runner (forced)
    ├─ debate ──► panel (seq/parallel) → consensus
    ├─ agentic ─► plan → tools/steps → critic
    └─ mixed ───► agentic → debate on findings
```

**Router location:** `src/intent_router.py` (heuristics first; optional `MODEL_FAST` classify for ambiguous mid/long text).  
**Chat path:** `src/chat_runner.py`. Wired in `create_job` + `process_job`. UI emits / shows `route_decided`.

---



## 5. System architecture (target / implemented shape)

```text
┌─────────────────────────────────────────────────────────────┐
│  Next.js UI (team browsers)                                 │
│  login · agents · models · enqueue job · live event feed    │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP + JWT + SSE
┌───────────────────────────▼─────────────────────────────────┐
│  FastAPI gateway  (web/server.py)                           │
│  /api/auth  /api/agents  /api/models  /api/jobs  /api/health│
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  Database (SQLite default · Postgres for team/cloud)        │
│  users · jobs · job_events · model_registry                 │
└───────────────────────────┬─────────────────────────────────┘
                            │ claim job
┌───────────────────────────▼─────────────────────────────────┐
│  Worker(s)  (in-process + optional worker_main.py)          │
│  intent route · chat · debate · agentic · LLM semaphore     │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│  LLM backends                                               │
│  Ollama local GPU · optional OpenAI-compatible / cloud tags │
└─────────────────────────────────────────────────────────────┘
```

**Why this shape helps**

- API stays thin and responsive (create job returns immediately)
- Workers can scale horizontally later without rewriting UI
- DB is the source of truth for queue + audit trail
- LLM lock protects the shared Quadro

---



## 6. Repository map (what each part does)


| Path                             | Purpose                               |
| -------------------------------- | ------------------------------------- |
| `main.py`                        | CLI debate + `--gui` API + `--worker` |
| `web/server.py`                  | FastAPI routes, auth, jobs SSE        |
| `worker_main.py`                 | Standalone worker process             |
| `src/config.py`                  | All env settings                      |
| `src/llm_client.py`              | Ollama + OpenAI-compatible chat       |
| `src/llm_lock.py`                | Global GPU concurrency gate           |
| `src/runner.py`                  | Debate panel → consensus              |
| `src/intent_router.py`           | Auto mode: chat vs debate vs agentic  |
| `src/chat_runner.py`             | Single-call Assistant path            |
| `src/agentic/`                   | Planner / tools / critic loop         |
| `src/jobs.py`                    | Create/claim/cancel jobs + events     |
| `src/worker.py`                  | Job execution loop                    |
| `src/auth.py`                    | Passwords + JWT                       |
| `src/db.py` / `src/models_db.py` | SQLAlchemy models                     |
| `src/model_registry.py`          | Discover/route models                 |
| `src/agent_store.py`             | Agent personas (JSON)                 |
| `frontend/`                      | Next.js team UI                       |
| `docker-compose.yml`             | Postgres + API + workers + Caddy      |
| `deploy/Caddyfile`               | TLS / reverse-proxy starter           |
| `requirments.md`                 | Original product brief                |
| `multi_agent_project_plan.md`    | Early checklist                       |
| `FULL_PROJECT_PLAN.md`           | **This document**                     |


---



## 7. End-to-end request lifecycle (how it works)

1. User signs in → JWT stored in browser (`multeagent_token`)
2. User picks mode (**Auto** default, or Chat / Debate / Agentic / Mixed), agents, optional model
3. UI `POST /api/jobs` → `classify_query` (if Auto) → job stored with **resolved** mode + `route_decided` event
4. Worker claims job (`queued` → `running`)
5. Worker runs chat / debate / agentic / mixed; emits `agent_start`, `tool_call`, `agent_done`, …
6. UI `GET /api/jobs/{id}/events` (SSE) streams those events live (shows Route chip)
7. Job finishes → `succeeded` / `failed` / `cancelled`
8. Transcript also lands under `logs/`; agentic files under `artifacts/{job_id}/`
9. Chat threads (localStorage) group multiple jobs into one conversation; soft-delete hides thread + jobs

**Why jobs beat “run inside the HTTP request”**

- Survives UI refresh
- Fair queue when 10 people click at once
- Cancel between steps
- Same protocol for one worker or many workers

---



## 8. Fairness & concurrency (team of 8–15)


| Control                    | Default | Why                                            |
| -------------------------- | ------- | ---------------------------------------------- |
| `LLM_SLOTS`                | 1       | One active generation on 8GB GPU               |
| `MAX_ACTIVE_JOBS_PER_USER` | 1       | Stops one person monopolizing queue            |
| `QUEUE_DEPTH_LIMIT`        | 40      | Backpressure when overloaded                   |
| Daily quota per user       | 50      | Soft protection of GPU time                    |
| Panel “parallel”           | Opt-in  | Logical parallelism; lock still serializes GPU |


**Important distinction**

- **Parallel agents** = multiple persona calls for one job  
- **Parallel users** = many jobs in the system

On one local GPU, user jobs should queue; agent parallel is optional and still gated by `LLM_SLOTS`.

---



## 9. Multi-model strategy

Models are synced from Ollama (`/api/tags`) into `model_registry`.


| Role     | Typical model               | Used for                               |
| -------- | --------------------------- | -------------------------------------- |
| `fast`   | `qwen2.5:3b`, `qwen3:1.7b`  | Planner, light panel                   |
| `strong` | `llama3.1:latest`           | Consensus, critic, hard reasoning      |
| `cloud`  | `*:cloud` tags / remote API | Overflow when local queue wait is high |


**Overflow policy**  
If estimated wait ≥ `OVERFLOW_WAIT_SECONDS` and a cloud model exists and user allows overflow → route that job’s models to cloud.

**Why this helps**

- Small tasks finish faster
- Strong model reserved for decisions
- Team keeps working when local GPU is busy

---



## 10. Agentic tools (safe by design)


| Tool             | What it does                      | Safety                           |
| ---------------- | --------------------------------- | -------------------------------- |
| `read_repo_file` | Read workspace file               | No `..` escape                   |
| `write_artifact` | Write under `artifacts/{job_id}/` | No arbitrary paths               |
| `http_get`       | Fetch URL                         | Host must be in `HTTP_ALLOWLIST` |
| `list_models`    | List Ollama tags                  | Read-only                        |
| `search_logs`    | Search past debate logs           | Read-only                        |


**Why no shell tool in v1**  
Shared team server + unrestricted shell = high risk. Add later only with hard sandboxing.

---



## 11. Auth & team access


| Feature   | Choice                    | Why                                         |
| --------- | ------------------------- | ------------------------------------------- |
| Login     | Local username/password   | Fast for office LAN                         |
| Tokens    | JWT (`PyJWT`)             | Stateless API auth                          |
| Passwords | `passlib` + `bcrypt`      | Standard hashing                            |
| Roles     | `admin` / `member`        | Admin can reset agents, edit model roles    |
| SSO       | Hook only (`SSO_ENABLED`) | Ready for cloud IdP later, not blocking LAN |


**Current testing policy:** only the bootstrap admin is used.  
Default: `admin` / `admin123`.  
`POST /api/auth/register` is **disabled** until the user module is built later.

---

## 12. Python stack — every library, why we use it



### 12.1 Libraries we use


| Library                            | Role                         | Why this one                          | Why helpful                         |
| ---------------------------------- | ---------------------------- | ------------------------------------- | ----------------------------------- |
| **Python 3.10+**                   | Language                     | Clear, good for orchestration scripts | Easy for the team to read/debug     |
| **requests**                       | HTTP to Ollama / tools       | Simple, sync, battle-tested           | Thin LLM client without SDK lock-in |
| **python-dotenv**                  | Load `.env`                  | Zero-friction config                  | Same code for laptop and server     |
| **FastAPI**                        | HTTP API                     | Async-friendly, OpenAPI docs, typed   | Clean team API surface              |
| **Uvicorn**                        | ASGI server                  | Standard FastAPI runner               | Production-capable process          |
| **Pydantic**                       | Request/response schemas     | Validation + clear errors             | Stops bad payloads early            |
| **SQLAlchemy 2**                   | ORM / DB access              | Works with SQLite **and** Postgres    | Hybrid path without rewrite         |
| **psycopg**                        | Postgres driver              | Modern driver for team/cloud DB       | Real concurrent queue later         |
| **PyJWT**                          | Issue/verify tokens          | Small, focused                        | Auth without a heavy auth server    |
| **passlib + bcrypt**               | Password hashing             | Common secure pattern                 | Don’t store plain passwords         |
| **threading** (stdlib)             | In-process worker + LLM lock | Enough for LAN v1                     | No Redis required to start          |
| **concurrent.futures**             | Panel parallel calls         | Simple thread pool                    | Optional agent parallelism          |
| **json / pathlib / uuid** (stdlib) | Data & files                 | No extra deps                         | Artifacts, payloads, IDs            |




### 12.2 Frontend stack


| Library        | Role             | Why                                          |
| -------------- | ---------------- | -------------------------------------------- |
| **Next.js**    | Team UI          | Modern app structure, easy LAN/cloud hosting |
| **React**      | Components       | Interactive control deck                     |
| **TypeScript** | Types            | Safer API client                             |
| **Tailwind**   | Styling baseline | Fast layout iteration                        |




### 12.3 Infra helpers


| Tool               | Role                             | Why                                  |
| ------------------ | -------------------------------- | ------------------------------------ |
| **Ollama**         | Local/cloud model runtime        | Already on your machine; simple HTTP |
| **Docker Compose** | Postgres + API + workers + Caddy | Repeatable team/cloud deploy         |
| **Caddy**          | Reverse proxy / TLS              | Simple HTTPS for hybrid/cloud        |


---



## 13. What we deliberately do NOT use (and why)


| Not using                                         | Why we skip it                                                           | When we might reconsider                                            |
| ------------------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------- |
| **LangChain**                                     | Heavy abstractions, prompt/token overhead, hides the loop we want to own | Only if integrating many external tool ecosystems                   |
| **LlamaIndex**                                    | Oriented to RAG apps, not our job queue                                  | If we add large document RAG later                                  |
| **CrewAI / AutoGen**                              | Opinionated multi-agent frameworks; harder to audit; extra deps          | If we need their specific collaboration patterns and accept lock-in |
| **Celery + Redis (phase 1)**                      | More moving parts; Postgres/SQLite jobs enough for 8–15 users            | If queue latency/pub-sub becomes a bottleneck                       |
| **Django**                                        | Heavier than needed for API+workers                                      | Unlikely                                                            |
| **Flask**                                         | Fine, but FastAPI gives better validation/docs for free                  | Unlikely                                                            |
| **Unbounded shell tools**                         | Dangerous on shared server                                               | Only with container sandbox                                         |
| **Running everything inside one browser request** | Breaks under team load                                                   | Never for production team use                                       |


**Core principle:** keep the **agent loop and prompts visible in our code**. Use libraries for HTTP, DB, auth — not for “magic agents.”

---



## 14. Comparison: our approach vs common alternatives



### 14.1 Orchestration


| Approach              | Pros                         | Cons                      | Verdict       |
| --------------------- | ---------------------------- | ------------------------- | ------------- |
| Own Python runner     | Transparent, loggable, light | You maintain the loop     | **Chosen**    |
| CrewAI/AutoGen        | Fast to demo                 | Opaque, heavy, harder ops | Avoid in core |
| Pure notebook scripts | Simple solo                  | No team queue/auth        | Not enough    |




### 14.2 Queue


| Approach       | Pros                             | Cons                         | Verdict                      |
| -------------- | -------------------------------- | ---------------------------- | ---------------------------- |
| DB jobs table  | One system for users+jobs+events | Slightly more SQL            | **Chosen** (SQLite→Postgres) |
| Redis/Celery   | Great at huge scale              | Extra service early          | Later if needed              |
| In-memory only | Easy                             | Lost on restart; one process | Demo only                    |




### 14.3 UI


| Approach                   | Pros                         | Cons                  | Verdict                        |
| -------------------------- | ---------------------------- | --------------------- | ------------------------------ |
| Next.js SPA talking to API | Team-ready, separable deploy | Two processes         | **Chosen**                     |
| FastAPI Jinja only         | One stack                    | Weaker interactive UX | Kept as legacy static fallback |
| Desktop Electron           | Native feel                  | Harder LAN sharing    | No                             |


---



## 15. Data model (mental model)


| Table            | Stores                                      | Why                                     |
| ---------------- | ------------------------------------------- | --------------------------------------- |
| `users`          | accounts, role, quota                       | Team identity                           |
| `jobs`           | mode, query, status, model_plan, timestamps | Queue + history                         |
| `job_events`     | ordered SSE payload JSON                    | Live UI + audit                         |
| `model_registry` | name, role, vram_class, enabled             | Routing policies                        |
| `agents/*.json`  | persona prompts (file store)                | Easy edit/export (can move to DB later) |


Job states: `queued → running → succeeded | failed | cancelled`

---



## 16. Phased delivery roadmap



### Phase A — Team foundation

- Jobs API + events SSE  
- Worker + LLM semaphore  
- JWT auth  
- Debate runs through jobs

**Done when:** two users submit; second waits in queue; both see live status.

### Phase B — Multi-model routing

- Sync Ollama models  
- fast/strong/cloud roles  
- Overflow when wait is high

**Done when:** light work uses small model; consensus uses strong model.

### Phase C — Agentic mode

- Planner / worker / critic  
- Safe tools + artifacts  
- UI modes: Debate | Agentic | Mixed

**Done when:** a goal produces plan → steps → critic (and optional debate).

### Phase C2 — Adaptive intent router (done)

- `src/intent_router.py` heuristics (+ optional fast-model classify)  
- `src/chat_runner.py` single Assistant path  
- Modes: **Auto** (default), Chat, Debate, Agentic, Mixed  
- `route_decided` event + UI route chip  
- Soft-delete conversations; chat threads aggregate jobs

**Done when:** `hi` and creative asks use chat; decision asks still debate; tool/research asks use agentic.

### Phase C3 — Vision + chat actions (done)

- Image paste/upload in the composer; attachments stored under `artifacts/{job_id}/images`
- `MODEL_VISION` + Ollama `capabilities` (`vision`) in the model registry
- Image + query always uses chat + a vision model (`force_vision_model`)
- Compact composer: attach icon, send icon, **Enter** to send
- **Edit** previous user message and resend; **Retry** regenerates the last turn

**Done when:** an image question answers with the vision model; Enter sends; edit/retry work in the same thread.

### Phase D — Cloud-ready hybrid

- Postgres + Docker Compose  
- Extra workers (`--worker` / `--scale`)  
- Caddy TLS proxy  
- SSO hook flag

**Done when:** same app can run off one LAN box or split API/workers/DB.

---



## 17. How to run (for reviewers)



### Local (typical)

```bash
# API
.\.venv\Scripts\activate
pip install -r requirements.txt
python main.py --gui

# UI
cd frontend
npm run dev
```

Login: `admin` / `admin123`

### Extra worker (horizontal)

```bash
python main.py --worker
```



### Postgres path

```bash
docker compose up -d db
# set DATABASE_URL in .env to postgres URL
python main.py --gui
```

---



## 18. Configuration cheat sheet


| Env var                                       | Meaning                            |
| --------------------------------------------- | ---------------------------------- |
| `LLM_BASE_URL`                                | Ollama base URL                    |
| `MODEL_FAST` / `MODEL_STRONG` / `MODEL_CLOUD` | Routing roles                      |
| `REMOTE_LLM_BASE_URL` / `REMOTE_LLM_API_KEY`  | Optional OpenAI-compatible backend |
| `DATABASE_URL`                                | SQLite or Postgres                 |
| `JWT_SECRET`                                  | Sign tokens (change in prod)       |
| `LLM_SLOTS`                                   | Concurrent LLM generations         |
| `OVERFLOW_WAIT_SECONDS`                       | When to prefer cloud               |
| `HTTP_ALLOWLIST`                              | Allowed hosts for `http_get`       |
| `API_HOST` / `API_PORT`                       | Bind address (`0.0.0.0` for LAN)   |
| `SSO_ENABLED`                                 | Expose SSO readiness hook          |


---



## 19. Risks & how we mitigate them


| Risk                  | Mitigation                                       |
| --------------------- | ------------------------------------------------ |
| GPU thrash            | `LLM_SLOTS`, per-user active job limit           |
| Runaway agentic loops | max steps / max tool rounds                      |
| Unsafe tools          | allowlist + artifact sandbox; no shell           |
| Secret leakage        | `.env` gitignored; change default admin password |
| Long SSE drops        | jobs persisted; UI can reconnect to job events   |
| Framework churn       | no LangChain/CrewAI in hot path                  |


---



## 20. How to improve next (after review)

1. **User module (later):** register, invite, roles UI, per-user history — after core modes are solid  
2. Move agents from JSON into DB (multi-user editing with audit)  
3. Admin dashboard: queue wait, tokens/sec, job failures  
4. Eval harness: golden prompts for consensus format  
5. Real SSO (Azure AD / Google) behind `SSO_ENABLED`  
6. Sandboxed code tool (optional) in containers  
7. Redis only if Postgres job claiming is not enough  
8. Production Next.js behind Caddy on one team URL  

---



## 21. Review checklist (for you / the team)

- [ ] Do we agree debate / agentic / mixed cover our use cases?  
- [ ] Is `LLM_SLOTS=1` right for our Quadro 8GB?  
- [ ] Which models should be `fast` vs `strong` vs `cloud`?  
- [ ] Is JWT + local accounts enough before SSO?  
- [ ] Which HTTP domains belong on `HTTP_ALLOWLIST`?  
- [ ] When do we switch SQLite → Postgres?  
- [ ] Who is admin, and did we change `admin123`?  

---

