# How a user query works in MulteAgent

This document explains, layer by layer, what happens when someone types a message (with or without an image) in the chat UI and presses Send.

---

## Big picture

```text
Browser (Next.js)
    │  POST /api/jobs  +  SSE /api/jobs/{id}/events
    ▼
FastAPI (web/server.py)
    │  auth → create_job → DB queue
    ▼
Intent router (src/intent_router.py)
    │  decides: chat | vision | image_gen | debate | agentic
    ▼
In-process worker (src/worker.py)
    │  claims job → runs the right path
    ▼
Executor
    │  chat_runner / image_gen (SDXL) / debate / agentic
    ▼
Events written to DB → SSE → UI feed updates
```

Everything shares one idea: **create a job, classify it, run it in the background, stream progress back as events.**

---

## Layer 1 — Frontend (what the user sees)

**Files:** `frontend/src/components/ControlDeck.tsx`, `frontend/src/lib/api.ts`, feed helpers

1. User types in the composer (and optionally attaches images).
2. On Send / Enter, the UI:
   - Shows the user bubble immediately (optimistic update).
   - Converts attached files to base64.
   - Calls `POST /api/jobs` with roughly:
     - `query` — the text
     - `mode` — Auto / Chat / Debate / Agentic / Mixed
     - `images[]` — optional attachments
     - model override, agent ids, etc.
3. After the job is created, the UI opens an **SSE stream** on  
   `GET /api/jobs/{job_id}/events`.
4. Each event updates the feed (status, agent bubbles, errors, image thumbs).
5. Generated images are loaded later via  
   `GET /api/jobs/{job_id}/attachments/{filename}`.

The frontend does **not** call Ollama or SDXL directly. It only talks to the API.

---

## Layer 2 — API / auth

**Files:** `web/server.py`, `src/auth.py`

1. Request must include `Authorization: Bearer <JWT>` (from login).
2. `POST /api/jobs` validates the body and calls `jobs.create_job(...)`.
3. On API startup (`python main.py --gui`), an **in-process worker** is started so queued jobs run without a separate process (unless you use `--worker` / scaled workers).

At this layer the system only cares about: *who is calling*, *is the payload valid*, *hand off to the job system*.

---

## Layer 3 — Job creation & queue

**File:** `src/jobs.py` → `create_job`

When a job is created, the backend does this in order:

| Step | What happens |
|------|----------------|
| 1. Guards | Per-user active jobs, queue depth, daily quota |
| 2. Classify | `classify_query(query, mode, has_images=...)` |
| 3. Model plan | Pick chat / vision / image labels (`resolve_model_plan`) |
| 4. Persist job | Row in DB with `status=queued`, mode, payload, model plan |
| 5. Save uploads | User images → `artifacts/{job_id}/images/` |
| 6. Events | `job_queued`, `route_decided` written for the SSE client |

Important rules here:

- If the user attached images and the intent is **not** image generation → pin a **vision** model (e.g. `llava`).
- If intent is **image_gen** → do **not** force vision; stamp the local SDXL label instead.
- Raw base64 is stripped from the stored payload; only attachment metadata / paths remain.

The job then waits until the worker **claims** it (`claim_next_job` → `status=running`).

---

## Layer 4 — Intent router (decide *what* to do)

**File:** `src/intent_router.py` → `classify_query`

This layer answers:

- **What kind of work is this?** (`intent`)
- **Which run mode?** (`resolved_mode`: chat / debate / agentic)
- **If drawing: txt2img or img2img?** (`image_mode`)

### Intents

| Intent | Meaning |
|--------|---------|
| `chat` | Normal text reply |
| `creative` | Story/poem-style chat |
| `vision` | Look at an attached image (needs Ollama vision model) |
| `image_gen` | Draw or edit a picture with local SDXL |
| `debate` | Optimist / Cynic / Consensus board |
| `agentic` | Planner + tools loop |

### Decision order (simplified)

```text
Has attached images?
  ├─ Edit / create / regenerate / “change X to Y”  →  image_gen + img2img
  ├─ “What’s in this?”, describe, read, OCR       →  vision
  └─ Default                                      →  vision

No images?
  ├─ Greeting / very short                        →  chat
  ├─ Manual mode selected (Debate, etc.)          →  honor mode
  │     (but image-draw wording still forces image_gen)
  ├─ Heuristics (regex): image / creative /
  │     agentic / debate / short Q&A              →  that intent
  ├─ Still unclear & message long enough          →  ask MODEL_FAST (Ollama)
  └─ Fallback                                     →  chat (or debate if long + decision-shaped)
```

Heuristics run **first** so Auto stays fast. The fast LLM classifier is only used when the message is ambiguous and long enough.

`image_gen` and `vision` both keep `resolved_mode = "chat"` so they go through the chat-shaped worker branch / UI, not the debate board.

---

## Layer 5 — Worker (decide *how* to run it)

**File:** `src/worker.py` → `process_job`

The worker loads the job, emits `route_decided` / progress events, then branches:

```text
intent == image_gen?
  → run_image_gen(...)          # local SDXL; early return

mode == chat?   (includes vision)
  → run_chat(... images=...)    # Ollama text or vision

mode == agentic?
  → run_agentic(...)

mode == mixed?
  → agentic research, then debate on the summary

else (debate)
  → run_session(...)            # multi-agent board
```

Every progress callback becomes a DB event that the SSE stream can deliver.

---

## Layer 6 — Executors (do the work)

### A) Chat / vision — `src/chat_runner.py`

- **Vision:** attached images + `MODEL_VISION` (e.g. `llava:7b`) via Ollama.
- **Chat:** normal text model (`MODEL_FAST` / strong / override).
- Must reach `LLM_BASE_URL` (default `http://localhost:11434`). If Ollama is down, vision/chat fails with a connection error.

### B) Image generation — `src/image_gen.py` + `src/sdxl_pipeline.py`

Does **not** use Ollama for drawing.

1. Optional **prompt polish** (`src/image_prompt.py`) — local text cleanup, no LLM.
2. Take the shared GPU lock (`llm_slot`) so chat and SDXL do not fight.
3. Load SDXL once per process (singleton):
   - **txt2img** — text only → new image  
   - **img2img** — attached image + text + `IMAGE_STRENGTH`
4. Save PNG under `artifacts/{job_id}/images/`.
5. Emit `agent_done` with `images: [{ filename, mime }]`.

Same checkpoint file for both pipelines (`MODEL_IMAGE_PATH` / default under `models/checkpoints/`). Memory-safe settings: fp16, CPU offload, VAE slicing, default ~768².

**Honesty check:** img2img re-draws from a noisy version of the photo. It is **not** surgical inpainting (e.g. changing one UI label with zero other changes). That would be a later feature.

### C) Debate — `src/runner.py`

Runs selected agents (Optimist, Cynic, …) then Consensus. Used for decisions / trade-offs, not for “draw a seahorse”.

### D) Agentic — `src/agentic/runtime.py`

Plan → tools → critic loop for research / multi-step work.

---

## Layer 7 — Shared infrastructure

| Piece | File | Role |
|-------|------|------|
| Config | `src/config.py` | Env / `.env` → runtime settings |
| LLM client | `src/llm_client.py` | Talks to Ollama-compatible API |
| GPU lock | `src/llm_lock.py` | `LLM_SLOTS` semaphore for LLM **and** SDXL |
| Attachments | `src/attachments.py` | Validate, save, load base64 / PIL |
| Events / DB | `src/jobs.py`, models | Job + event log for SSE and history |

### Important env knobs (images)

| Variable | Role |
|----------|------|
| `MODEL_IMAGE_PATH` | Path to SDXL `.safetensors` |
| `IMAGE_WIDTH` / `IMAGE_HEIGHT` | Default 768 |
| `IMAGE_STEPS` / `IMAGE_GUIDANCE` | Quality vs speed |
| `IMAGE_STRENGTH` | img2img change amount (e.g. 0.35–0.55) |
| `IMAGE_PROMPT_POLISH` | Local prompt cleanup on/off |
| `MODEL_VISION` | Vision chat model name |
| `MODEL_FAST` | Fast classifier / light chat |
| `LLM_BASE_URL` | Ollama (or compatible) base URL |
| `LLM_SLOTS` | Concurrent GPU “slots” |
| `ARTIFACTS_DIR` | Where images and job files live |

---

## Layer 8 — Events back to the UI

Typical stream for an image job:

```text
job_queued
route_decided          ← intent, image_mode, reason
job_started
session_start
agent_start            ← “Image generation” / “Image edit (img2img)”
agent_done             ← text + images[]
session_done
```

The feed reducer turns these into bubbles. For generated images, `AgentThumbs` fetches each file from the attachments API and shows a large preview.

---

## Worked examples

### 1) “create an image of a seahorse in Antarctica”

1. UI → `POST /api/jobs` (no images).
2. Router heuristics → `image_gen`, `image_mode=txt2img`.
3. Worker → `run_image_gen` → polish prompt → SDXL txt2img.
4. PNG saved → SSE → thumb in feed.  
   **Ollama not required.**

### 2) Attach Compass screenshot + “only change MongoDB to MySQL and regenerate”

1. Images saved as attachments.
2. Router → `image_gen`, `image_mode=img2img` (not vision).
3. Worker → img2img with strength from config.
4. New PNG streamed back.  
   Layout may shift; not a perfect text-only edit.

### 3) Attach screenshot + “what’s in this image?”

1. Router → `vision`.
2. Worker → `run_chat` with images + `llava` (or `MODEL_VISION`).
3. Needs **Ollama running**. If not → connection error in the status bar.

### 4) “Should we expand into the EU market next year?”

1. Router → `debate` (decision / business signals).
2. Worker → multi-agent debate session.
3. Optimist / Cynic / Consensus bubbles appear over SSE.

---

## Mental model (one sentence per layer)

| Layer | One-liner |
|-------|-----------|
| UI | Capture message, create job, listen for events. |
| API | Authenticate and accept the job. |
| Jobs | Classify, plan models, store files, enqueue. |
| Router | Decide *what kind* of work and *how* to draw. |
| Worker | Claim job and dispatch to the right runner. |
| Executor | Call Ollama and/or local SDXL; emit progress. |
| Lock / artifacts | Fair GPU use; persist images. |
| SSE / feed | Stream results so the chat updates live. |

---

## Related docs

- Quick start & image setup: [README.md](README.md)
- Speed / efficiency checklist (tiers, status): [IMPROVEMENT_ROADMAP.md](IMPROVEMENT_ROADMAP.md)
- Broader product / architecture notes: [FULL_PROJECT_PLAN.md](FULL_PROJECT_PLAN.md)
