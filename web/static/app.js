const state = {
  agents: [],
  selected: new Set(),
  running: false,
};

const els = {
  healthStatus: document.getElementById("healthStatus"),
  agentList: document.getElementById("agentList"),
  feed: document.getElementById("feed"),
  debateForm: document.getElementById("debateForm"),
  queryInput: document.getElementById("queryInput"),
  parallelToggle: document.getElementById("parallelToggle"),
  modeLabel: document.getElementById("modeLabel"),
  modeHint: document.getElementById("modeHint"),
  selectionMeta: document.getElementById("selectionMeta"),
  btnRun: document.getElementById("btnRun"),
  btnClearChat: document.getElementById("btnClearChat"),
  btnNewAgent: document.getElementById("btnNewAgent"),
  btnResetAgents: document.getElementById("btnResetAgents"),
  agentModal: document.getElementById("agentModal"),
  agentForm: document.getElementById("agentForm"),
  modalTitle: document.getElementById("modalTitle"),
  editAgentId: document.getElementById("editAgentId"),
  fName: document.getElementById("fName"),
  fRole: document.getElementById("fRole"),
  fStage: document.getElementById("fStage"),
  fAccent: document.getElementById("fAccent"),
  fPrompt: document.getElementById("fPrompt"),
  fEnabled: document.getElementById("fEnabled"),
  btnDeleteAgent: document.getElementById("btnDeleteAgent"),
  btnCloseModal: document.getElementById("btnCloseModal"),
  btnCancelModal: document.getElementById("btnCancelModal"),
};

function showEmptyFeed() {
  els.feed.innerHTML = `
    <div class="empty-feed">
      <strong>No debate yet</strong>
      Select agents, choose sequential or parallel, then ask a business question.
    </div>
  `;
}

function appendBubble({ kind, title, meta, body, accent, pending }) {
  const empty = els.feed.querySelector(".empty-feed");
  if (empty) empty.remove();

  const node = document.createElement("article");
  node.className = `bubble ${kind}${pending ? " pending" : ""}`;
  if (accent) node.style.setProperty("--accent", accent);

  const head = document.createElement("div");
  head.className = "bubble-head";
  head.innerHTML = `<strong>${escapeHtml(title || "")}</strong><span>${escapeHtml(meta || "")}</span>`;

  const content = document.createElement("div");
  content.className = "bubble-body";
  content.textContent = body || "";

  node.append(head, content);
  els.feed.appendChild(node);
  els.feed.scrollTop = els.feed.scrollHeight;
  return node;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = data.detail || detail;
    } catch (_) {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (response.status === 204) return null;
  return response.json();
}

async function refreshHealth() {
  try {
    const data = await api("/api/health");
    els.healthStatus.classList.toggle("ok", data.llm_reachable);
    els.healthStatus.classList.toggle("bad", !data.llm_reachable);
    els.healthStatus.querySelector(".status-text").textContent = data.llm_reachable
      ? `${data.model} ready`
      : `LLM offline · ${data.base_url}`;
  } catch (err) {
    els.healthStatus.classList.remove("ok");
    els.healthStatus.classList.add("bad");
    els.healthStatus.querySelector(".status-text").textContent = "API unreachable";
  }
}

function updateModeLabels() {
  const parallel = els.parallelToggle.checked;
  els.modeLabel.textContent = parallel ? "Parallel" : "Sequential";
  els.modeHint.textContent = parallel
    ? "Panel agents fire together · may stress 8GB VRAM"
    : "VRAM-safe · one agent at a time";
}

function updateSelectionMeta() {
  const panelSelected = state.agents.filter(
    (a) => a.stage === "panel" && a.enabled && state.selected.has(a.id)
  ).length;
  const consensusSelected = state.agents.filter(
    (a) => a.stage === "consensus" && a.enabled && state.selected.has(a.id)
  ).length;
  els.selectionMeta.textContent = `${panelSelected} panel · ${consensusSelected} consensus selected`;
}

function renderAgents() {
  els.agentList.innerHTML = "";
  for (const agent of state.agents) {
    const li = document.createElement("li");
    li.className = `agent-item${agent.enabled ? "" : " disabled"}`;

    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "agent-check";
    check.checked = state.selected.has(agent.id);
    check.disabled = !agent.enabled;
    check.addEventListener("change", () => {
      if (check.checked) state.selected.add(agent.id);
      else state.selected.delete(agent.id);
      updateSelectionMeta();
    });

    const meta = document.createElement("div");
    meta.className = "agent-meta";
    meta.innerHTML = `
      <strong>${escapeHtml(agent.name)}</strong>
      <span>${escapeHtml(agent.role || "No role")}</span>
      <span class="badge ${agent.stage}">${agent.stage}</span>
    `;
    meta.style.cursor = "pointer";
    meta.title = "Edit agent";
    meta.addEventListener("click", () => openModal(agent));

    const chip = document.createElement("div");
    chip.className = "accent-chip";
    chip.style.background = agent.accent || "#6b8cae";

    li.append(check, meta, chip);
    els.agentList.appendChild(li);
  }
  updateSelectionMeta();
}

async function loadAgents() {
  state.agents = await api("/api/agents");
  if (state.selected.size === 0) {
    for (const agent of state.agents) {
      if (agent.enabled) state.selected.add(agent.id);
    }
  } else {
    const valid = new Set(state.agents.map((a) => a.id));
    state.selected = new Set([...state.selected].filter((id) => valid.has(id)));
  }
  renderAgents();
}

function openModal(agent = null) {
  els.editAgentId.value = agent?.id || "";
  els.modalTitle.textContent = agent ? "Edit agent" : "New agent";
  els.fName.value = agent?.name || "";
  els.fRole.value = agent?.role || "";
  els.fStage.value = agent?.stage || "panel";
  els.fAccent.value = agent?.accent || "#6b8cae";
  els.fPrompt.value = agent?.system_prompt || "";
  els.fEnabled.checked = agent?.enabled ?? true;
  els.btnDeleteAgent.hidden = !agent;
  els.agentModal.showModal();
  els.fName.focus();
}

function closeModal() {
  if (els.agentModal.open) els.agentModal.close();
}

els.btnNewAgent.addEventListener("click", () => openModal());
els.btnCloseModal.addEventListener("click", closeModal);
els.btnCancelModal.addEventListener("click", closeModal);
els.parallelToggle.addEventListener("change", updateModeLabels);
els.btnClearChat.addEventListener("click", showEmptyFeed);

els.btnResetAgents.addEventListener("click", async () => {
  if (!confirm("Restore the default Optimist, Cynic, and Consensus agents?")) return;
  await api("/api/agents/reset", { method: "POST", body: "{}" });
  state.selected.clear();
  await loadAgents();
});

els.btnDeleteAgent.addEventListener("click", async () => {
  const id = els.editAgentId.value;
  if (!id || !confirm("Delete this agent?")) return;
  await api(`/api/agents/${encodeURIComponent(id)}`, { method: "DELETE" });
  state.selected.delete(id);
  closeModal();
  await loadAgents();
});

els.agentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = {
    name: els.fName.value.trim(),
    role: els.fRole.value.trim(),
    stage: els.fStage.value,
    accent: els.fAccent.value,
    system_prompt: els.fPrompt.value.trim(),
    enabled: els.fEnabled.checked,
  };
  const id = els.editAgentId.value;
  try {
    if (id) {
      await api(`/api/agents/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
    } else {
      const created = await api("/api/agents", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.selected.add(created.id);
    }
    closeModal();
    await loadAgents();
  } catch (err) {
    alert(err.message || String(err));
  }
});

els.debateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.running) return;

  const query = els.queryInput.value.trim();
  if (!query) return;
  if (state.selected.size === 0) {
    alert("Select at least one agent.");
    return;
  }

  const mode = els.parallelToggle.checked ? "parallel" : "sequential";
  const pendingNodes = new Map();

  state.running = true;
  els.btnRun.disabled = true;
  els.btnRun.textContent = "Running…";

  appendBubble({
    kind: "user",
    title: "You",
    meta: mode,
    body: query,
  });

  try {
    const response = await fetch("/api/debate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        mode,
        agent_ids: [...state.selected],
      }),
    });

    if (!response.ok || !response.body) {
      throw new Error("Debate request failed.");
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";

      for (const chunk of chunks) {
        const line = chunk
          .split("\n")
          .filter((part) => part.startsWith("data:"))
          .map((part) => part.slice(5).trim())
          .join("");
        if (!line) continue;
        const event = JSON.parse(line);
        handleEvent(event, pendingNodes);
      }
    }
  } catch (err) {
    appendBubble({
      kind: "system",
      title: "Error",
      body: err.message || String(err),
    });
  } finally {
    state.running = false;
    els.btnRun.disabled = false;
    els.btnRun.textContent = "Run debate";
  }
});

function handleEvent(event, pendingNodes) {
  if (event.type === "session_start") {
    appendBubble({
      kind: "system",
      title: "Session",
      body: `${event.mode} · model ${event.model}`,
    });
    return;
  }

  if (event.type === "agent_start") {
    const node = appendBubble({
      kind: "agent",
      title: event.name,
      meta: `${event.role} · thinking`,
      body: "Waiting for local model…",
      accent: event.accent,
      pending: true,
    });
    pendingNodes.set(event.agent_id, node);
    return;
  }

  if (event.type === "agent_done") {
    let node = pendingNodes.get(event.agent_id);
    if (!node) {
      node = appendBubble({
        kind: "agent",
        title: event.name,
        meta: event.role,
        body: event.output,
        accent: event.accent,
      });
    } else {
      node.classList.remove("pending");
      node.querySelector(".bubble-head span").textContent =
        `${event.role} · ${Number(event.elapsed_seconds || 0).toFixed(1)}s`;
      node.querySelector(".bubble-body").textContent = event.output;
    }
    pendingNodes.delete(event.agent_id);
    els.feed.scrollTop = els.feed.scrollHeight;
    return;
  }

  if (event.type === "agent_error" || event.type === "fatal") {
    appendBubble({
      kind: "system",
      title: "Agent error",
      body: event.error || "Unknown error",
    });
    return;
  }

  if (event.type === "session_done") {
    const errText = event.errors?.length ? ` · errors: ${event.errors.join("; ")}` : "";
    appendBubble({
      kind: "system",
      title: "Complete",
      body: `${Number(event.total_seconds || 0).toFixed(1)}s · log ${event.log_path}${errText}`,
    });
  }
}

showEmptyFeed();
updateModeLabels();
refreshHealth();
loadAgents();
setInterval(refreshHealth, 15000);
