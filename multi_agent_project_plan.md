# Multi-Agent Consensus System — Project Plan

> **Read this for the complete review guide (architecture, libraries, why/why-not):**  
> **[FULL_PROJECT_PLAN.md](FULL_PROJECT_PLAN.md)**

Local, framework-free multi-agent debate powered by one local LLM.
Agents run **sequentially** (Optimist → Cynic → Consensus) via plain HTTP.

---

## Step 0 — Foundations (this document + scaffold)

- [x] Write this plan
- [x] Create project layout, `requirements.txt`, config, `.env.example`
- [x] Confirm local LLM endpoint (default: Ollama at `http://localhost:11434`)

**Exit criteria:** Repo structure exists; config points at a reachable model.

---

## Step 1 — LLM client

- [x] Thin HTTP client (`urllib` / `requests` only — no agent frameworks)
- [x] Chat completion helper with retries and clear errors
- [x] Model / base URL / temperature from env or config

**Exit criteria:** One scripted prompt round-trips to the local model.

---

## Step 2 — Agent personas & prompts

| Agent | Role | Focus |
|-------|------|--------|
| 1 Optimist / Visionary | Upside case | Speed, efficiency, operational gains |
| 2 Cynic / Risk Analyst | Attack case | Risks, compliance, hidden costs |
| 3 Consensus / Executive | Decision | Feasibility matrix + mitigations |

- [x] Distinct system prompts (no shared “corporate” voice)
- [x] Strict output shapes (bullet counts, sections)

**Exit criteria:** Each agent returns structured text when called alone.

---

## Step 3 — Sequential pipeline

```
User query
  → Agent 1 (optimist)     → upside context
  → Agent 2 (cynic)        → full debate log
  → Agent 3 (consensus)    → final recommendation
```

- [x] Pass prior outputs as context (context injection)
- [x] Never run agents in parallel (8GB VRAM constraint)

**Exit criteria:** One end-to-end debate on a sample business question.

---

## Step 4 — Logging & traceability

- [x] Timestamped log file per run under `logs/`
- [x] Capture: query, each agent raw output, timings, model name

**Exit criteria:** Every run leaves an auditable transcript.

---

## Step 5 — CLI

- [x] `python main.py "Your business question"`
- [x] Optional flags: `--model`, `--temperature`, `--log-dir`

**Exit criteria:** Usable from terminal without editing code.

---

## Smoke test (done)

- [x] End-to-end debate with `llama3.1:latest` via Ollama (~66s)
- [x] Transcript written under `logs/`

## Step 6 — GUI + parallel management

- [x] Agent store (create / edit / delete / reset defaults)
- [x] Parallel + sequential panel execution
- [x] FastAPI backend with SSE debate stream
- [x] Next.js control deck (`frontend/`) — chat, manage agents, parallel toggle

## Step 7 — Hardening (later)

- [ ] Domain experts (optional): Compliance Auditor, Financial Analyst
- [ ] Automated evaluation matrix scoring moderator output
- [ ] Longer context / truncated history strategy if tokens blow up

---

## Critical constraints (do not violate)

1. **No** CrewAI / AutoGen / LangChain in v1
2. Prefer **sequential** on 8GB VRAM; parallel is opt-in
3. **Constrained** prompts (exact sections / bullet counts)
4. **Log everything** for regression tracing

---

## Suggested first test query

> Should our mid-size logistics company adopt an AI-assisted route planner for last-mile delivery in Q3?

---

## Hardware notes

- Target: Intel Xeon + 8GB Quadro
- Clear heavy GPU/CPU jobs before long debates
- Prefer smaller/quantized local models if VRAM is tight
