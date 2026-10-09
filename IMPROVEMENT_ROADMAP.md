# MulteAgent flow improvements — living checklist

Track optimizations from the speed/efficiency plan. Check items as they land.  
Related context: [HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md).

**How we work:** complete tiers in order (1 → 2 → 3 → 4). Mark `[x]` only when code is in the repo.

---

## Tier 1 — High impact (GPU + cold start)

- [x] **T1.1** Warm SDXL pipeline at API startup (`warm_pipeline_async`, health `image_pipeline`)
- [x] **T1.2** Unload Ollama models before image jobs (`src/ollama_mgmt.py`, `IMAGE_UNLOAD_OLLAMA`)
- [x] **T1.3** Fast vs quality image profiles (`IMAGE_PROFILE=fast|balanced|quality` + UI Profile select)
- [x] **T1.4** Expand intent heuristics; LLM classify only for long debate/agentic ambiguity
- [x] **T1.5** Optional TinyVAE (`IMAGE_TINY_VAE=true` → `madebyollin/taesdxl`)

---

## Tier 2 — Quality + fewer retries

- [x] **T2.1** Prompt polish only when short/vague (`should_polish` in `image_prompt.py`)
- [x] **T2.2** Img2img strength presets in UI (Keep close / Medium / Restyle)
- [x] **T2.3** Explicit **Describe** / **Generate** / **Edit** / **Inpaint** composer actions (`force_intent`)
- [x] **T2.4** Inpainting path (`generate_inpaint`; attach source + `mask_*` image, white=repaint)

---

## Tier 3 — Architecture speed

- [x] **T3.1** Event notify for SSE (`src/job_events.py` + `wait_job` instead of blind sleep)
- [x] **T3.2** Separate `llm_slot` and `image_slot` (`IMAGE_SLOTS`; image holds both so chat waits fairly)
- [x] **T3.3** Dedicated image worker — use existing `python main.py --worker` / scale workers; in-process warm remains default for single-box
- [x] **T3.4** Cancel aborts in-flight diffusion (`callback_on_step_end` + `pipe._interrupt`)

---

## Tier 4 — Later / hardware-bound

- [x] **T4.1** Documented: distilled SDXL (Turbo/Lightning) — try only with Pascal-safe torch; see notes below
- [x] **T4.2** Documented: ControlNet / structure-lock for UI mockups needs extra VRAM — defer on 8GB
- [x] **T4.3** Documented: Postgres + `docker compose --scale worker=N` for multi-user queue (not single-GPU latency)

### Tier 4 notes

| Topic | Guidance |
|-------|----------|
| Distilled SDXL | Swap `MODEL_IMAGE_PATH` to a Lightning/Turbo safetensors **only after** validating `torch.cuda` kernels on P4000 (cu118). Lower steps (4–8). |
| ControlNet | Adds another UNet-sized load; usually too tight with offload on 8GB. Prefer stronger prompts + img2img first. |
| Scale workers | Helps many users waiting in queue; one GPU still serializes image jobs via `image_slot`. |

---

## Progress log

| Date | Item | Notes |
|------|------|--------|
| 2026-10-09 | Checklist created | Started Tier 1 |
| 2026-10-09 | T1–T3 implemented | Warm-load, unload Ollama, profiles, heuristics, TinyVAE opt, polish gate, UI actions/presets, inpaint+mask, SSE notify, dual locks, cancel interrupt |
| 2026-10-09 | T4 documented | Hardware-bound options recorded; no ControlNet ship on 8GB |

---

## Env quick reference (new)

```env
IMAGE_PROFILE=fast
IMAGE_WARM_ON_START=true
IMAGE_UNLOAD_OLLAMA=true
IMAGE_TINY_VAE=false
IMAGE_SLOTS=1
```

Restart API after changing these. Watch **Image model: warming → ready** under the composer.
