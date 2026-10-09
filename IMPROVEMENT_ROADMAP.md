# MulteAgent flow improvements — living checklist

Track optimizations and new capabilities. Check items as they land.  
Related: [HOW_QUERY_WORKS.md](HOW_QUERY_WORKS.md).

**How we work:** complete sections in order. Mark `[x]` only when code is in the repo.

---

## Tier 1 — High impact (GPU + cold start)

- [x] **T1.1–T1.5** Warm SDXL, unload Ollama, profiles, heuristics, TinyVAE

---

## Tier 2 — Quality + fewer retries

- [x] **T2.1–T2.4** Prompt polish gate, strength presets, Describe/Generate/Edit/Inpaint

---

## Tier 3 — Architecture speed

- [x] **T3.1–T3.4** SSE notify, dual locks, worker scale notes, cancel interrupt

---

## Tier 4 — Later / hardware-bound

- [x] **T4.1–T4.3** Documented (Turbo / ControlNet / multi-worker)

---

## Tier 5 — Downloadable documents & files

Goal: user asks for Word / PDF / PPT / HTML / Excel / zip → agentic tools write files → chat shows **Download** chips.

### Phase A — foundation

- [x] **D5.A1** `src/artifacts.py` — safe write/list/resolve + mime map
- [x] **D5.A2** Tools: `write_html`, `write_docx`, `list_artifacts` (+ `write_artifact`)
- [x] **D5.A3** Register artifacts on job payload + `artifact_ready` SSE
- [x] **D5.A4** Download API for any job file
- [x] **D5.A5** UI download chips in feed
- [x] **D5.A6** Intent `doc_gen` → agentic; tools in worker prompt; **Document** button

### Phase B — office pack

- [x] **D5.B1** `write_pdf` (fpdf2)
- [x] **D5.B2** `write_pptx` (python-pptx)
- [x] **D5.B3** `write_xlsx` (openpyxl)
- [x] **D5.B4** `package_zip`

### Phase C — polish

- [x] **D5.C1** Richer templates (report / one-pager / pitch)
- [x] **D5.C2** Embed generated PNG into DOCX/PPTX/PDF (+ HTML data URI)
- [x] **D5.C3** History replay of artifact chips from job payload

---

## Progress log

| Date | Item | Notes |
|------|------|--------|
| 2026-10-09 | T1–T4 | Image/speed work complete |
| 2026-10-09 | Tier 5 A+B | HTML/DOCX/PDF/PPTX/XLSX/ZIP tools, downloads, Document button, doc_gen routing |
| 2026-10-09 | Tier 5 C | Templates, image embed, history artifact chips |

---

## How to use documents

1. Restart API after `pip install -r requirements.txt` (adds python-docx, pptx, openpyxl, fpdf2).
2. Type e.g. `create a PDF report about Q3 sales` (Auto → agentic) **or** click **Document**.
3. When a file is written, a **↓ filename** chip appears — click to download.
4. Inpaint-style mask naming does not apply; docs live under `artifacts/{job_id}/files/`.

```env
IMAGE_PROFILE=fast
IMAGE_WARM_ON_START=true
IMAGE_UNLOAD_OLLAMA=true
```
