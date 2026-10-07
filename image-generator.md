In-project SDXL image generation (analysis + plan)

Goal

Type “create an image…” in MulteAgent chat → worker generates a PNG with your local SDXL weights → image appears in the feed. No ComfyUI dependency at runtime. Comfy stays optional as a manual test bench only.

What we already have (context)





Intent routing already flags image asks as image_gen in [src/intent_router.py](src/intent_router.py).



Worker already branches to [src/image_gen.py](src/image_gen.py) in [src/worker.py](src/worker.py).



UI already can show generated attachments (agent_done.images + thumbs).



Gap: run_image_gen still calls Ollama /v1/images/generations, which does not work on this Windows/Ollama setup.



Hardware reality: Quadro P4000, 8GB, compute capability 6.1 (Pascal). Newer CUDA 13 PyTorch builds fail with no kernel image is available. Must pin PyTorch + CUDA 11.8 (or another Pascal-supporting wheel) in the MulteAgent venv.

flowchart LR
  userMsg[User_message] --> createJob[create_job]
  createJob --> router[intent_router]
  router -->|image_gen| worker[worker]
  worker --> imgGen[image_gen.run_image_gen]
  imgGen --> pipe[Diffusers_SDXL_singleton]
  pipe --> artifacts[artifacts_job_images]
  artifacts --> sse[SSE_agent_done_images]
  sse --> ui[ControlDeck_thumbs]

Decision (locked)







Choice



Decision





Backend



Hugging Face Diffusers inside the API/worker process





Model



Local sd_xl_base_1.0.safetensors via StableDiffusionXLPipeline.from_single_file





Comfy



Not required at runtime





Video



Out of scope for this phase





GPU sharing



Reuse existing [llm_slot](src/llm_lock.py) so chat LLM and image gen never fight

What we need

Software (MulteAgent .venv)





torch / torchvision — cu118 build that supports sm_61 (same class of fix applied in Comfy)



diffusers, transformers, accelerate, safetensors, pillow



Optional later: xformers only if a Pascal-compatible wheel exists; otherwise skip

Model assets





Checkpoint path (env): e.g. MODEL_IMAGE_PATH=C:\...\sd_xl_base_1.0.safetensors
Prefer a copy or hardlink under D:\multeagent\models\checkpoints\ so the app is self-contained.



First load may fetch SDXL config/tokenizer pieces from HF cache unless we vendor them; plan for online first load, then local_files_only / cached files.

Config ([src/config.py](src/config.py), [.env.example](.env.example))





MODEL_IMAGE_PATH — absolute path to .safetensors



IMAGE_WIDTH / IMAGE_HEIGHT — default 768 (safer on 8GB; 1024 optional)



IMAGE_STEPS — default 25



IMAGE_GUIDANCE — default 7.0



IMAGE_DTYPE — float16 (not bf16 on Pascal)



Keep MODEL_IMAGE unused or repurpose as display label

Case studies — issues people hit (and we will avoid)







Issue



Who hits it



Fix we will bake in





Vision ≠ generate



You already hit this with llava



Keep image_gen route; never send draw prompts to chat/vision models





CUDA: no kernel image on Pascal



You hit this in Comfy with torch cu130



Pin torch cu118; document in README; smoke-test tensor.cuda() at startup





OOM at 1024² on 8GB



Common SDXL reports



Default 768²; enable_model_cpu_offload(); enable_vae_slicing(); never .to("cuda") before offload





Calling .to("cuda") + offload



Diffusers docs / Félix Sanz guide



Offload owns placement; do not double-move





Slow every request (reload 7GB)



Typical first implementations



Process-wide singleton pipeline; load once, reuse under llm_slot





from_single_file tries to download configs offline



HF discussions / StackOverflow



Allow first-time cache; ship or cache configs; set local_files_only after warm





LLM + image both on GPU



Team LAN box



Exclusive llm_slot; queue image jobs like chat jobs





Safety checker / watermarker RAM



Diffusers SDXL defaults



Disable watermarker for local use; keep optional safety later





Windows pagefile thrash with offload



SD.Next / community



Recommend ≥32GB system RAM / pagefile note in README

What we will do (implementation)





Deps — extend [requirements.txt](requirements.txt) with pinned torch cu118 + diffusers stack; document Windows install command (index URL).



src/sdxl_pipeline.py (new) — lazy singleton:





get_pipeline(config) loads once from MODEL_IMAGE_PATH



fp16 + enable_model_cpu_offload() + VAE slicing



generate(prompt, negative, w, h, steps, guidance) -> PIL.Image



Rewrite [src/image_gen.py](src/image_gen.py) — if path missing → clear “configure MODEL_IMAGE_PATH” message; else generate under llm_slot, save via [save_images](src/attachments.py), emit existing SSE image events.



Worker — keep current image_gen branch; no mode change needed.



Config/docs — .env.example, README “Image generation” section: path, VRAM tips, Pascal torch note, “stop Ollama load while generating”.



Verification — CLI/smoke: one prompt → PNG in artifacts/; then UI “create an image of a seahorse…” shows thumb.

Efficiency / optimization strategy (v1)

Ordered by impact on this machine:





Singleton pipeline (biggest latency win after first image)



fp16 + model CPU offload + VAE slicing (fits 8GB)



Default 768², 20–25 steps, DPM++ / UniPC scheduler (speed/quality balance)



Shared llm_slot (stability over parallel thrash)



Later (not v1): TinyVAE, torch.compile (often weak/broken on Pascal), refiner pass, LoRAs

Out of scope (this phase)





Video generation



ComfyUI API bridge



Multi-GPU / cloud image APIs



Training / fine-tuning



Fancy UI size/steps controls (env defaults first; Settings UI later)

Success criteria





Prompt matching image-gen heuristics produces a PNG without Comfy running.



Feed shows the generated image.



No CUDA kernel error on Quadro P4000 with documented torch pin.



Concurrent text job waits on llm_slot instead of crashing GPU.

