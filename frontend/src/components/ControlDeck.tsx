"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
} from "react";
import {
  API_ORIGIN,
  cancelJob,
  createAgent,
  createJob,
  deleteAgent,
  getHealth,
  getToken,
  listAgents,
  listJobs,
  listModels,
  login,
  me,
  resetAgents,
  setToken,
  streamJobEvents,
  updateAgent,
} from "@/lib/api";
import type {
  Agent,
  AgentInput,
  AuthUser,
  DebateEvent,
  FeedItem,
  Health,
  Job,
  ModelInfo,
  RunMode,
} from "@/lib/types";
import MarkdownBody from "@/components/MarkdownBody";

const emptyForm: AgentInput = {
  name: "",
  role: "",
  system_prompt: "",
  stage: "panel",
  enabled: true,
  accent: "#6b8cae",
};

function uid() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function ControlDeck() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authUser, setAuthUser] = useState("");
  const [authPass, setAuthPass] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);

  const [agents, setAgents] = useState<Agent[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [parallel, setParallel] = useState(false);
  const [runMode, setRunMode] = useState<RunMode>("debate");
  const [modelOverride, setModelOverride] = useState("");
  const [allowOverflow, setAllowOverflow] = useState(true);
  const [query, setQuery] = useState("");
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [running, setRunning] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<AgentInput>(emptyForm);

  const selectionMeta = useMemo(() => {
    const panel = agents.filter((a) => a.stage === "panel" && a.enabled && selected.has(a.id)).length;
    const consensus = agents.filter(
      (a) => a.stage === "consensus" && a.enabled && selected.has(a.id),
    ).length;
    return `${panel} panel · ${consensus} consensus`;
  }, [agents, selected]);

  const loadAgents = useCallback(async () => {
    const data = await listAgents();
    setAgents(data);
    setSelected((prev) => {
      if (prev.size === 0) return new Set(data.filter((a) => a.enabled).map((a) => a.id));
      const valid = new Set(data.map((a) => a.id));
      return new Set([...prev].filter((id) => valid.has(id)));
    });
  }, []);

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await getHealth());
      setHealthError(null);
    } catch (err) {
      setHealth(null);
      setHealthError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const refreshModels = useCallback(async (refresh = false) => {
    setModels(await listModels(refresh));
  }, []);

  const refreshJobs = useCallback(async () => {
    setJobs(await listJobs());
  }, []);

  useEffect(() => {
    refreshHealth();
    const timer = setInterval(refreshHealth, 15000);
    return () => clearInterval(timer);
  }, [refreshHealth]);

  useEffect(() => {
    if (!getToken()) return;
    me()
      .then(async (u) => {
        setUser(u);
        await loadAgents();
        await refreshModels();
        await refreshJobs();
      })
      .catch(() => setToken(null));
  }, [loadAgents, refreshModels, refreshJobs]);

  async function handleAuth(event: FormEvent) {
    event.preventDefault();
    setAuthError(null);
    try {
      // Multi-user accounts come later — default admin is enough for testing.
      const u = await login(authUser.trim(), authPass);
      setUser(u);
      await loadAgents();
      await refreshModels(true);
      await refreshJobs();
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : String(err));
    }
  }

  function logout() {
    setToken(null);
    setUser(null);
    setAgents([]);
    setJobs([]);
    setFeed([]);
  }

  function toggleSelected(id: string, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function openCreate() {
    setEditingId(null);
    setForm(emptyForm);
    setModalOpen(true);
  }

  function openEdit(agent: Agent) {
    setEditingId(agent.id);
    setForm({
      name: agent.name,
      role: agent.role,
      system_prompt: agent.system_prompt,
      stage: agent.stage,
      enabled: agent.enabled,
      accent: agent.accent || "#6b8cae",
    });
    setModalOpen(true);
  }

  async function saveAgent(event: FormEvent) {
    event.preventDefault();
    try {
      if (editingId) await updateAgent(editingId, form);
      else {
        const created = await createAgent(form);
        setSelected((prev) => new Set(prev).add(created.id));
      }
      setModalOpen(false);
      await loadAgents();
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    }
  }

  async function removeAgent() {
    if (!editingId || !confirm("Delete this agent?")) return;
    await deleteAgent(editingId);
    setSelected((prev) => {
      const next = new Set(prev);
      next.delete(editingId);
      return next;
    });
    setModalOpen(false);
    await loadAgents();
  }

  function pushFeed(item: Omit<FeedItem, "id"> & { id?: string }) {
    setFeed((prev) => [...prev, { ...item, id: item.id || uid() }]);
  }

  function handleEvent(event: DebateEvent) {
    const type = String(event.type || "");
    if (type === "job_queued") {
      pushFeed({
        kind: "system",
        title: "Queued",
        body: `wait ~${Number(event.estimated_wait_seconds || 0).toFixed(0)}s · depth ${event.queue_depth}`,
      });
      return;
    }
    if (type === "job_started" || type === "session_start" || type === "agentic_start") {
      pushFeed({
        kind: "system",
        title: type,
        body: JSON.stringify(
          {
            mode: event.mode,
            model: event.model,
            model_plan: event.model_plan,
            goal: event.goal,
          },
          null,
          0,
        ),
      });
      return;
    }
    if (type === "agent_start") {
      pushFeed({
        kind: "agent",
        title: String(event.name || "Agent"),
        meta: `${event.role || ""} · thinking`,
        body: "Waiting for model…",
        accent: String(event.accent || "#3aa89a"),
        pending: true,
        agentId: String(event.agent_id || ""),
      });
      return;
    }
    if (type === "tool_call") {
      pushFeed({
        kind: "system",
        title: `Tool ${event.tool}`,
        body: JSON.stringify(event.args ?? {}, null, 2),
      });
      return;
    }
    if (type === "tool_result") {
      pushFeed({
        kind: "system",
        title: `Tool result · ${event.tool}`,
        body: String(event.result || ""),
      });
      return;
    }
    if (type === "agent_done") {
      setFeed((prev) => {
        const hasPending = prev.some((item) => item.agentId === event.agent_id && item.pending);
        if (!hasPending) {
          return [
            ...prev,
            {
              id: uid(),
              kind: "agent",
              title: String(event.name || "Agent"),
              meta: `${event.role || ""} · ${Number(event.elapsed_seconds || 0).toFixed(1)}s`,
              body: String(event.output || ""),
              accent: String(event.accent || "#3aa89a"),
              agentId: String(event.agent_id || ""),
            },
          ];
        }
        return prev.map((item) =>
          item.agentId === event.agent_id && item.pending
            ? {
                ...item,
                pending: false,
                meta: `${event.role || ""} · ${Number(event.elapsed_seconds || 0).toFixed(1)}s`,
                body: String(event.output || ""),
              }
            : item,
        );
      });
      return;
    }
    if (type === "agent_error" || type === "fatal" || type === "job_cancelled") {
      pushFeed({
        kind: "system",
        title: type,
        body: String(event.error || "cancelled"),
      });
      return;
    }
    if (type === "session_done" || type === "agentic_done") {
      const errors = Array.isArray(event.errors) ? event.errors.join("; ") : "";
      pushFeed({
        kind: "system",
        title: "Complete",
        body: `${Number(event.total_seconds || 0).toFixed(1)}s · ${event.log_path || "ok"}${
          errors ? ` · ${errors}` : ""
        }`,
      });
    }
  }

  async function runDebate(event: FormEvent) {
    event.preventDefault();
    if (running) return;
    const text = query.trim();
    if (!text) return;
    if (runMode !== "agentic" && selected.size === 0) {
      alert("Select at least one agent for debate/mixed mode.");
      return;
    }

    setRunning(true);
    setError(null);
    pushFeed({ kind: "user", title: "You", meta: `${runMode} · ${parallel ? "parallel" : "sequential"}`, body: text });

    try {
      const job = await createJob({
        query: text,
        mode: runMode,
        execution_mode: parallel ? "parallel" : "sequential",
        agent_ids: [...selected],
        model: modelOverride || undefined,
        allow_overflow: allowOverflow,
      });
      setActiveJobId(job.id);
      pushFeed({
        kind: "system",
        title: "Job created",
        body: `${job.id} · status ${job.status}`,
      });
      await streamJobEvents(job.id, handleEvent);
      await refreshJobs();
    } catch (err) {
      pushFeed({
        kind: "system",
        title: "Error",
        body: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setRunning(false);
      setActiveJobId(null);
    }
  }

  if (!user) {
    return (
      <div className="app-shell">
        <header className="topbar">
          <div className="brand">
            <span className="brand-mark" aria-hidden />
            <div>
              <h1>MulteAgent</h1>
              <p>Team control deck · {API_ORIGIN}</p>
            </div>
          </div>
          <div className={`status ${health?.llm_reachable ? "ok" : "bad"}`}>
            <span className="dot" />
            <span>
              {health
                ? health.llm_reachable
                  ? `${health.model} · queue ${health.queue_depth ?? 0}`
                  : `LLM offline`
                : healthError || "API unreachable"}
            </span>
          </div>
        </header>

        <form className="auth-card" onSubmit={handleAuth}>
          <h2>Sign in</h2>
          <p className="hint">
            Team user accounts come later. For now use the default admin:
            <br />
            <strong>admin / admin123</strong>
          </p>
          <label>
            Username
            <input
              value={authUser}
              onChange={(e) => setAuthUser(e.target.value)}
              placeholder="admin"
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={authPass}
              onChange={(e) => setAuthPass(e.target.value)}
              placeholder="admin123"
              required
            />
          </label>
          {authError ? <p className="banner">{authError}</p> : null}
          <div className="composer-row">
            <span />
            <button type="submit" className="btn primary">
              Sign in
            </button>
          </div>
        </form>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden />
          <div>
            <h1>MulteAgent</h1>
            <p>
              {user.username} · {user.role}
            </p>
          </div>
        </div>
        <div className="top-actions">
          <div className={`status ${health?.llm_reachable ? "ok" : "bad"}`}>
            <span className="dot" />
            <span>
              {health
                ? `${health.model} · q ${health.queue_depth ?? 0} · wait ~${Number(
                    health.estimated_wait_seconds || 0,
                  ).toFixed(0)}s`
                : healthError || "API unreachable"}
            </span>
          </div>
          <button type="button" className="btn ghost" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>

      {error ? <p className="banner">{error}</p> : null}

      <main className="layout">
        <aside className="rail">
          <div className="rail-head">
            <h2>Agents</h2>
            <button type="button" className="btn ghost" onClick={openCreate}>
              New
            </button>
          </div>
          <p className="hint">Panel agents can queue in parallel mode. Consensus waits.</p>
          <ul className="agent-list">
            {agents.map((agent) => (
              <li key={agent.id} className={`agent-item${agent.enabled ? "" : " disabled"}`}>
                <input
                  type="checkbox"
                  checked={selected.has(agent.id)}
                  disabled={!agent.enabled}
                  onChange={(e) => toggleSelected(agent.id, e.target.checked)}
                />
                <button type="button" className="agent-meta" onClick={() => openEdit(agent)}>
                  <strong>{agent.name}</strong>
                  <span>{agent.role || "No role"}</span>
                  <span className={`badge ${agent.stage}`}>{agent.stage}</span>
                </button>
                <span className="accent-chip" style={{ background: agent.accent }} />
              </li>
            ))}
          </ul>
          <div className="rail-block">
            <div className="rail-head">
              <h2>Models</h2>
              <button type="button" className="btn ghost" onClick={() => refreshModels(true)}>
                Sync
              </button>
            </div>
            <select value={modelOverride} onChange={(e) => setModelOverride(e.target.value)}>
              <option value="">Auto route (fast/strong/cloud)</option>
              {models
                .filter((m) => m.enabled)
                .map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name} · {m.role}/{m.vram_class}
                  </option>
                ))}
            </select>
            <label className="check">
              <input
                type="checkbox"
                checked={allowOverflow}
                onChange={(e) => setAllowOverflow(e.target.checked)}
              />
              Cloud overflow when queue waits
            </label>
          </div>
          <div className="rail-block">
            <h2>Recent jobs</h2>
            <ul className="job-list">
              {jobs.slice(0, 8).map((job) => (
                <li key={job.id}>
                  <strong>{job.status}</strong> · {job.mode}
                  <span>{job.query.slice(0, 48)}</span>
                </li>
              ))}
            </ul>
          </div>
          {user.role === "admin" ? (
            <button
              type="button"
              className="btn ghost full"
              onClick={async () => {
                if (!confirm("Restore default agents?")) return;
                await resetAgents();
                setSelected(new Set());
                await loadAgents();
              }}
            >
              Restore default agents
            </button>
          ) : null}
        </aside>

        <section className="stage">
          <div className="stage-toolbar">
            <div className="mode-row">
              <select value={runMode} onChange={(e) => setRunMode(e.target.value as RunMode)}>
                <option value="debate">Debate</option>
                <option value="agentic">Agentic</option>
                <option value="mixed">Mixed</option>
              </select>
              <label className="mode-switch">
                <input
                  type="checkbox"
                  checked={parallel}
                  onChange={(e) => setParallel(e.target.checked)}
                />
                <span className="switch-ui" />
                <span className="switch-label">
                  <strong>{parallel ? "Parallel panel" : "Sequential panel"}</strong>
                  <small>GPU still gated by server LLM slots</small>
                </span>
              </label>
            </div>
            <div className="toolbar-actions">
              {activeJobId ? (
                <button
                  type="button"
                  className="btn danger ghost"
                  onClick={() => cancelJob(activeJobId)}
                >
                  Cancel job
                </button>
              ) : null}
              <button type="button" className="btn ghost" onClick={() => setFeed([])}>
                Clear feed
              </button>
            </div>
          </div>

          <div className="feed">
            {feed.length === 0 ? (
              <div className="empty">
                <strong>No run yet</strong>
                Choose Debate, Agentic, or Mixed — then submit a goal. Jobs are queued for the team.
              </div>
            ) : (
              feed.map((item) => (
                <article
                  key={item.id}
                  className={`bubble ${item.kind}${item.pending ? " pending" : ""}`}
                  style={
                    item.accent
                      ? ({ ["--accent"]: item.accent } as CSSProperties)
                      : undefined
                  }
                >
                  <div className="bubble-head">
                    <strong>{item.title}</strong>
                    <span>{item.meta}</span>
                  </div>
                  <div className="bubble-body">{item.body}</div>
                </article>
              ))
            )}
          </div>

          <form className="composer" onSubmit={runDebate}>
            <textarea
              rows={3}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={
                runMode === "agentic"
                  ? "Describe a goal for the agentic planner/worker loop…"
                  : "Ask a business question for the panel…"
              }
              required
            />
            <div className="composer-row">
              <p>
                {selectionMeta} · mode {runMode}
              </p>
              <button type="submit" className="btn primary" disabled={running}>
                {running ? "Running…" : "Enqueue run"}
              </button>
            </div>
          </form>
        </section>
      </main>

      {modalOpen ? (
        <div className="modal-backdrop" role="presentation" onClick={() => setModalOpen(false)}>
          <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={saveAgent}>
            <header>
              <h3>{editingId ? "Edit agent" : "New agent"}</h3>
              <button type="button" className="icon-btn" onClick={() => setModalOpen(false)}>
                ×
              </button>
            </header>
            <label>
              Name
              <input
                required
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </label>
            <label>
              Role
              <input
                value={form.role}
                onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}
              />
            </label>
            <label>
              Stage
              <select
                value={form.stage}
                onChange={(e) =>
                  setForm((f) => ({ ...f, stage: e.target.value as AgentInput["stage"] }))
                }
              >
                <option value="panel">Panel</option>
                <option value="consensus">Consensus</option>
              </select>
            </label>
            <label>
              Accent
              <input
                type="color"
                value={form.accent}
                onChange={(e) => setForm((f) => ({ ...f, accent: e.target.value }))}
              />
            </label>
            <label>
              System prompt
              <textarea
                required
                rows={10}
                value={form.system_prompt}
                onChange={(e) => setForm((f) => ({ ...f, system_prompt: e.target.value }))}
              />
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked }))}
              />
              Enabled
            </label>
            <footer>
              {editingId ? (
                <button type="button" className="btn danger ghost" onClick={removeAgent}>
                  Delete
                </button>
              ) : (
                <span />
              )}
              <div className="footer-right">
                <button type="button" className="btn ghost" onClick={() => setModalOpen(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn primary">
                  Save agent
                </button>
              </div>
            </footer>
          </form>
        </div>
      ) : null}
    </div>
  );
}
