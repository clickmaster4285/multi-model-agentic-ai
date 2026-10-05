Adaptive intent router (pre-Ollama)

Problem

Today the UI mode is manual only. Debates always use business personas ([src/prompts.py](src/prompts.py)), and agentic always runs planner/worker/critic + tools — so "hi" costs ~50s and a story request becomes a business feasibility memo.

Approach

Insert a fast intent layer before any multi-agent Ollama work. Default UI mode becomes Auto. Manual Debate / Agentic / Mixed remain as overrides.

flowchart TD
  msg[User message] --> createJob[create_job]
  createJob --> router[intent_router.classify]
  router -->|greeting_chat_creative| chat[Single LLM chat]
  router -->|decision_business| debate[run_session debate]
  router -->|tools_research_multistep| agentic[run_agentic]
  router -->|manual_override| forced[Use selected mode]
  chat --> done[session_done]
  debate --> done
  agentic --> done

Classifier strategy (no extra LLM for common cases):





Heuristics first (instant): greetings, thanks, short chitchat, creative-writing verbs (write a story, poem, joke), simple factual how-to when short.



Ambiguous mid/long queries only: one tiny MODEL_FAST classify call returning JSON {intent, confidence} — skip if heuristics already confident.



Map intent → resolved mode:





chat / creative → new chat path (1 LLM call)



debate → existing Optimist/Cynic/Consensus



agentic → existing planner/worker/critic



Manual UI mode ≠ auto → never re-route

Backend changes

1. [src/intent_router.py](src/intent_router.py) (new)





classify_query(query, *, requested_mode, config) -> RouteDecision



Fields: resolved_mode, intent, reason, used_llm: bool



Heuristic rules + optional fast-model fallback with strict JSON parse and safe default (chat for short, debate for long business-like text)

2. New single-call runner [src/chat_runner.py](src/chat_runner.py)





Friendly general assistant system prompt (not business board)



Emits the same event shapes the UI already understands: agent_start / agent_done / session_done (one “Assistant” card)



Uses model_plan["worker"] or MODEL_FAST

3. Hook in [src/jobs.py](src/jobs.py) create_job





Accept mode: auto | debate | agentic | mixed | chat



When auto (or missing): run classify_query, store resolved mode on the job, stash route meta in payload_json (intent, reason)



Emit early event via first worker progress: route_decided so the UI can show a compact chip (“Auto → chat · greeting”)

4. Branch in [src/worker.py](src/worker.py) process_job





Add mode == "chat" → run_chat(...)



Keep existing agentic / mixed / debate branches



At start of every job, append route_decided from payload if present

5. API / types





[web/server.py](web/server.py) JobCreatePayload.mode include auto and chat



[src/jobs.py](src/jobs.py) validation allowlist update

Frontend changes

6. Mode select in [ControlDeck.tsx](frontend/src/components/ControlDeck.tsx)





Default runMode = "auto"



Options: Auto · Chat · Debate · Agentic · Mixed



Chat/Auto do not require agent selection



Show route chip when feed gets route_decided (via [feedReducer.ts](frontend/src/lib/feedReducer.ts) → compact status)

7. Types [frontend/src/lib/types.ts](frontend/src/lib/types.ts)





Extend RunMode with "auto" | "chat"

Small related fixes (same pass)





Planner [object Object]: format plan JSON/text in agentic progress events ([src/agentic/runtime.py](src/agentic/runtime.py)) so the UI shows readable steps



Keep tool allowlist as-is for now; chat path simply never calls tools on greetings

What we will not do in this pass





No embedding/vector classifier



No prompt rewrite of Optimist/Cynic for creative tasks (chat path replaces that)



No change to soft-delete / threads

Verification





Auto + "hi" → one short Assistant reply in a few seconds, no planner/tools



Auto + story request → story with emojis via chat, not business consensus



Auto + “Should we expand into market X? risks and recommendation” → debate



Auto + “research this URL and summarize then draft a plan” → agentic



Manual Debate still forces full board even on "hi"

