"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type MouseEvent,
} from "react";
import {
  getApiOrigin,
  cancelJob,
  createAgent,
  createJob,
  deleteAgent,
  getHealth,
  getJobHistory,
  getToken,
  listAgents,
  listJobs,
  listModels,
  login,
  me,
  resetAgents,
  setToken,
  softDeleteJobs,
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
import MessageAvatar from "@/components/MessageAvatar";
import {
  appendUserMessage,
  buildFeedFromEvents,
  reduceFeed,
  STATUS_ID,
  upsertStatus,
} from "@/lib/feedReducer";
import {
  activeProjectId,
  assignJobToProject,
  createProject,
  deleteProject,
  listProjects,
  setActiveProjectId,
  type Project,
} from "@/lib/projects";
import {
  addJobToThread,
  createThread,
  ensureThreadsForJobs,
  getActiveThreadId,
  listThreads,
  pinThread,
  setActiveThreadId,
  softDeleteThread,
  type ChatThread,
} from "@/lib/threads";

const emptyForm: AgentInput = {
  name: "",
  role: "",
  system_prompt: "",
  stage: "panel",
  enabled: true,
  accent: "#6b8cae",
};

function titleFromQuery(query: string) {
  const t = query.trim().replace(/\s+/g, " ");
  return t.length > 42 ? `${t.slice(0, 42)}…` : t || "Untitled chat";
}

export default function ControlDeck() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [authUser, setAuthUser] = useState("admin");
  const [authPass, setAuthPass] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);

  const [agents, setAgents] = useState<Agent[]>([]);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [parallel, setParallel] = useState(false);
  const [runMode, setRunMode] = useState<RunMode>("auto");
  const [modelOverride, setModelOverride] = useState("");
  const [allowOverflow, setAllowOverflow] = useState(true);
  const [query, setQuery] = useState("");
  const [feed, setFeed] = useState<FeedItem[]>([]);
  const [chatTitle, setChatTitle] = useState("New chat");
  const [running, setRunning] = useState(false);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [activeThreadId, setActiveThreadIdState] = useState<string | null>(null);
  const [historyFilter, setHistoryFilter] = useState("");
  const [sidebarTab, setSidebarTab] = useState<"history" | "projects">("history");

  const [settingsOpen, setSettingsOpen] = useState(false);
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

  const jobById = useMemo(() => new Map(jobs.map((j) => [j.id, j])), [jobs]);

  const filteredThreads = useMemo(() => {
    let list = threads;
    if (projectId) {
      const project = projects.find((p) => p.id === projectId);
      const projectJobs = new Set(project?.jobIds || []);
      list = list.filter(
        (t) => t.projectId === projectId || t.jobIds.some((id) => projectJobs.has(id)),
      );
    }
    const q = historyFilter.trim().toLowerCase();
    if (q) {
      list = list.filter((t) => {
        if (t.title.toLowerCase().includes(q)) return true;
        return t.jobIds.some((id) => {
          const job = jobById.get(id);
          return job
            ? job.query.toLowerCase().includes(q) || job.status.includes(q)
            : false;
        });
      });
    }
    return list;
  }, [threads, projectId, projects, historyFilter, jobById]);

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
    const data = await listJobs(100);
    setJobs(data);
    setThreads(ensureThreadsForJobs(data));
    return data;
  }, []);

  const selectThreadId = useCallback((id: string | null) => {
    setActiveThreadIdState(id);
    setActiveThreadId(id);
  }, []);

  const openThread = useCallback(
    async (thread: ChatThread) => {
      selectThreadId(thread.id);
      setChatTitle(thread.title);
      setActiveJobId(thread.jobIds[thread.jobIds.length - 1] || null);
      setFeed([
        {
          id: `loading-${thread.id}`,
          kind: "system",
          title: "Loading",
          body: thread.jobIds.length
            ? `Fetching ${thread.jobIds.length} message${thread.jobIds.length === 1 ? "" : "s"}…`
            : "Empty chat",
        },
      ]);
      try {
        let built: FeedItem[] = [];
        const errors: string[] = [];
        for (const jobId of thread.jobIds) {
          try {
            const history = await getJobHistory(jobId);
            const slice = buildFeedFromEvents(history.events, history.job.query).filter(
              (item) => item.id !== STATUS_ID,
            );
            built = [
              ...built,
              ...slice.map((item) =>
                item.kind === "user"
                  ? { ...item, meta: `${history.job.mode} · ${history.job.status}` }
                  : item,
              ),
            ];
          } catch (err) {
            const job = jobById.get(jobId);
            errors.push(err instanceof Error ? err.message : String(err));
            if (job) {
              built = appendUserMessage(
                built,
                job.query,
                `${job.mode} · ${job.status} · history unavailable`,
              );
            }
          }
        }
        // Keep the user's mode preference (usually Auto); don't sticky-switch to debate.
        if (built.length === 0 && thread.jobIds.length === 0) {
          setFeed([]);
        } else if (errors.length && built.length === 0) {
          setFeed([
            {
              id: `err-${thread.id}`,
              kind: "system",
              title: "Could not load chat",
              body: errors[0],
              meta: "error",
            },
          ]);
        } else {
          setFeed(built);
        }
      } catch (err) {
        setFeed([
          {
            id: `err-${thread.id}`,
            kind: "system",
            title: "Error",
            body: err instanceof Error ? err.message : String(err),
            meta: "error",
          },
        ]);
      }
    },
    [jobById, selectThreadId],
  );

  useEffect(() => {
    refreshHealth();
    const timer = setInterval(refreshHealth, 15000);
    return () => clearInterval(timer);
  }, [refreshHealth]);

  useEffect(() => {
    setProjects(listProjects());
    setProjectId(activeProjectId());
    setThreads(listThreads());
    setActiveThreadIdState(getActiveThreadId());
  }, []);

  useEffect(() => {
    if (!getToken()) return;
    me()
      .then(async (u) => {
        setUser(u);
        await loadAgents();
        await refreshModels();
        await refreshJobs();
      })
      .catch(() => {
        setToken(null);
        setUser(null);
      });
  }, [loadAgents, refreshModels, refreshJobs]);

  // If a request cleared the token (401), leave the chat shell and show login.
  useEffect(() => {
    if (user && !getToken()) {
      setUser(null);
      setAgents([]);
      setJobs([]);
      setFeed([]);
    }
  }, [user, feed.length, running]);

  function applyEvent(event: DebateEvent) {
    setFeed((prev) => reduceFeed(prev, event));
  }

  async function handleAuth(event: FormEvent) {
    event.preventDefault();
    setAuthError(null);
    try {
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
    selectThreadId(null);
  }

  function startNewChat() {
    const thread = createThread("New chat", projectId);
    setThreads(listThreads());
    selectThreadId(thread.id);
    setFeed([]);
    setQuery("");
    setActiveJobId(null);
    setChatTitle("New chat");
    setRunning(false);
  }

  async function handleDeleteThread(thread: ChatThread, event: MouseEvent) {
    event.stopPropagation();
    if (!confirm(`Delete chat “${thread.title}”? Related messages will be hidden.`)) return;
    try {
      if (thread.jobIds.length > 0) {
        await softDeleteJobs(thread.jobIds);
      }
      setThreads(softDeleteThread(thread.id));
      setJobs((prev) => prev.filter((j) => !thread.jobIds.includes(j.id)));
      if (activeThreadId === thread.id) {
        selectThreadId(null);
        setFeed([]);
        setChatTitle("New chat");
        setActiveJobId(null);
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : String(err));
    }
  }

  function handlePinThread(thread: ChatThread, event: MouseEvent) {
    event.stopPropagation();
    setThreads(pinThread(thread.id));
  }

  function handleNewProject() {
    const name = prompt("Project name");
    if (!name?.trim()) return;
    const project = createProject(name);
    setProjects(listProjects());
    setProjectId(project.id);
    setActiveProjectId(project.id);
    setSidebarTab("projects");
  }

  function selectProject(id: string | null) {
    setProjectId(id);
    setActiveProjectId(id);
    setSidebarTab("history");
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

  async function runDebate(event: FormEvent) {
    event.preventDefault();
    if (running) return;
    const text = query.trim();
    if (!text) return;
    if (runMode === "debate" || runMode === "mixed") {
      if (selected.size === 0) {
        alert("Select at least one agent in Settings.");
        setSettingsOpen(true);
        return;
      }
    }

    let threadId = activeThreadId;
    if (!threadId) {
      const thread = createThread(titleFromQuery(text), projectId);
      threadId = thread.id;
      selectThreadId(threadId);
    }

    setRunning(true);
    setChatTitle(titleFromQuery(text));
    setFeed((prev) =>
      appendUserMessage(prev, text, `${runMode} · ${parallel ? "parallel" : "sequential"}`),
    );
    setQuery("");

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
      setThreads(addJobToThread(threadId, job.id, titleFromQuery(text)));
      if (projectId) {
        setProjects(assignJobToProject(projectId, job.id));
      }
      setFeed((prev) => upsertStatus(prev, "Queued", `${job.status} · ${job.id.slice(0, 8)}…`));
      await streamJobEvents(job.id, applyEvent);
      await refreshJobs();
    } catch (err) {
      setFeed((prev) =>
        upsertStatus(prev, "Error", err instanceof Error ? err.message : String(err), true),
      );
    } finally {
      setRunning(false);
    }
  }

  if (!user) {
    return (
      <div className="app-shell auth-shell">
        <form className="auth-card" onSubmit={handleAuth}>
          <div className="brand">
            <span className="brand-mark" aria-hidden />
            <div>
              <h1>MulteAgent</h1>
              <p>Sign in to continue</p>
            </div>
          </div>
          <p className="hint">
            Testing login: <strong>admin / admin123</strong>
          </p>
          <label>
            Username
            <input value={authUser} onChange={(e) => setAuthUser(e.target.value)} required />
          </label>
          <label>
            Password
            <input
              type="password"
              value={authPass}
              onChange={(e) => setAuthPass(e.target.value)}
              required
            />
          </label>
          {authError ? <p className="banner">{authError}</p> : null}
          <div className={`status ${health?.llm_reachable ? "ok" : "bad"}`}>
            <span className="dot" />
            <span>
              {health
                ? health.llm_reachable
                  ? `${health.model} ready`
                  : "LLM offline"
                : healthError || `API · ${getApiOrigin()}`}
            </span>
          </div>
          <button type="submit" className="btn primary full">
            Sign in
          </button>
        </form>
      </div>
    );
  }

  const activeProject = projects.find((p) => p.id === projectId) || null;

  return (
    <div className="chat-app">
      <aside className="chat-rail" aria-label="Chat navigation">
        <div className="rail-brand">
          <span className="brand-mark sm" aria-hidden />
          <div>
            <strong>MulteAgent</strong>
            <span>{user.username}</span>
          </div>
        </div>

        <button type="button" className="btn primary full new-chat-btn" onClick={startNewChat}>
          New chat
        </button>

        <div className="rail-tabs">
          <button
            type="button"
            className={sidebarTab === "history" ? "active" : ""}
            onClick={() => setSidebarTab("history")}
          >
            History
          </button>
          <button
            type="button"
            className={sidebarTab === "projects" ? "active" : ""}
            onClick={() => setSidebarTab("projects")}
          >
            Projects
          </button>
        </div>

        {sidebarTab === "projects" ? (
          <div className="rail-section">
            <div className="rail-section-head">
              <h2>Projects</h2>
              <button type="button" className="btn ghost sm" onClick={handleNewProject}>
                New
              </button>
            </div>
            <button
              type="button"
              className={`nav-item ${projectId ? "" : "active"}`}
              onClick={() => selectProject(null)}
            >
              All chats
            </button>
            {projects.map((p) => (
              <div key={p.id} className={`nav-item row ${projectId === p.id ? "active" : ""}`}>
                <button type="button" className="nav-main" onClick={() => selectProject(p.id)}>
                  <strong>{p.name}</strong>
                  <span>{p.jobIds.length} chats</span>
                </button>
                <button
                  type="button"
                  className="icon-btn"
                  title="Delete project"
                  onClick={() => {
                    if (!confirm(`Delete project “${p.name}”?`)) return;
                    setProjects(deleteProject(p.id));
                    if (projectId === p.id) selectProject(null);
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        ) : (
          <div className="rail-section grow">
            <div className="rail-section-head">
              <h2>{activeProject ? activeProject.name : "History"}</h2>
              <button type="button" className="btn ghost sm" onClick={() => refreshJobs()}>
                Refresh
              </button>
            </div>
            <input
              className="search-input"
              placeholder="Search chats…"
              value={historyFilter}
              onChange={(e) => setHistoryFilter(e.target.value)}
            />
            <ul className="history-list">
              {filteredThreads.length === 0 ? (
                <li className="empty-side">No chats yet. Start a new one.</li>
              ) : (
                filteredThreads.map((thread) => {
                  const lastJob = [...thread.jobIds]
                    .reverse()
                    .map((id) => jobById.get(id))
                    .find(Boolean);
                  const turns = thread.jobIds.length;
                  return (
                    <li key={thread.id}>
                      <div
                        className={`nav-item row ${activeThreadId === thread.id ? "active" : ""} ${thread.pinned ? "pinned" : ""}`}
                      >
                        <button
                          type="button"
                          className="nav-main"
                          onClick={() => openThread(thread)}
                        >
                          <strong>{thread.title}</strong>
                          <span>
                            {turns} turn{turns === 1 ? "" : "s"}
                            {lastJob ? ` · ${lastJob.status}` : ""}
                          </span>
                        </button>
                        <button
                          type="button"
                          className={`icon-btn ${thread.pinned ? "on" : ""}`}
                          title={thread.pinned ? "Unpin chat" : "Pin chat"}
                          onClick={(e) => handlePinThread(thread, e)}
                        >
                          {thread.pinned ? "★" : "☆"}
                        </button>
                        <button
                          type="button"
                          className="icon-btn danger"
                          title="Delete chat"
                          onClick={(e) => handleDeleteThread(thread, e)}
                        >
                          ×
                        </button>
                      </div>
                    </li>
                  );
                })
              )}
            </ul>
          </div>
        )}

        <div className="rail-footer">
          <button type="button" className="btn ghost full" onClick={() => setSettingsOpen(true)}>
            Settings · agents & models
          </button>
          <div className={`status ${health?.llm_reachable ? "ok" : "bad"}`}>
            <span className="dot" />
            <span>
              {health
                ? `q ${health.queue_depth ?? 0} · ~${Number(health.estimated_wait_seconds || 0).toFixed(0)}s`
                : "API down"}
            </span>
          </div>
          <button type="button" className="btn ghost full" onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>

      <main className="chat-main">
        <header className="chat-top">
          <div>
            <h2>{chatTitle}</h2>
            <p>
              {runMode} · {selectionMeta}
              {activeProject ? ` · ${activeProject.name}` : ""}
            </p>
          </div>
          <div className="chat-controls">
            <select value={runMode} onChange={(e) => setRunMode(e.target.value as RunMode)}>
              <option value="auto">Auto</option>
              <option value="chat">Chat</option>
              <option value="debate">Debate</option>
              <option value="agentic">Agentic</option>
              <option value="mixed">Mixed</option>
            </select>
            <select value={modelOverride} onChange={(e) => setModelOverride(e.target.value)}>
              <option value="">Auto model route</option>
              {models
                .filter((m) => m.enabled)
                .map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name}
                  </option>
                ))}
            </select>
            <label className="mode-switch compact">
              <input
                type="checkbox"
                checked={parallel}
                onChange={(e) => setParallel(e.target.checked)}
              />
              <span className="switch-ui" />
              <span>{parallel ? "Parallel" : "Sequential"}</span>
            </label>
            {activeJobId && running ? (
              <button
                type="button"
                className="btn danger ghost"
                onClick={() => cancelJob(activeJobId)}
              >
                Cancel
              </button>
            ) : null}
          </div>
        </header>

        <div className="feed chat-feed">
          {feed.length === 0 ? (
            <div className="empty hero-empty">
              <strong>Start a conversation</strong>
              Say hi, ask a question, request a story, or run a full debate — Auto picks the path.
            </div>
          ) : (
            feed.map((item, index) => {
              const isError =
                item.kind === "system" && (item.meta === "error" || item.title === "Error");
              // Skip consecutive duplicate user bubbles (same text)
              if (
                item.kind === "user" &&
                index > 0 &&
                feed[index - 1].kind === "user" &&
                feed[index - 1].body.trim() === item.body.trim()
              ) {
                return null;
              }
              return (
                <article
                  key={item.id}
                  className={`msg-row ${item.kind}${item.pending ? " pending" : ""}${
                    isError ? " error" : ""
                  }`}
                  style={
                    item.accent ? ({ ["--accent"]: item.accent } as CSSProperties) : undefined
                  }
                >
                  <div className="msg-card">
                    {item.kind === "system" ? (
                      <p className="msg-line">
                        <span className="msg-label">{item.title}</span>
                        {item.body ? <span className="msg-meta"> {item.body}</span> : null}
                      </p>
                    ) : (
                      <>
                        {item.kind === "agent" ? (
                          <header className="msg-head">
                            <MessageAvatar kind="agent" title={item.title} accent={item.accent} />
                            <p className="msg-caption">
                              <span className="msg-label">{item.title}</span>
                              {item.meta ? <span className="msg-meta"> · {item.meta}</span> : null}
                            </p>
                          </header>
                        ) : null}
                        {item.kind === "user" ? (
                          <div className="bubble-body">{item.body}</div>
                        ) : (
                          <MarkdownBody content={item.body} pending={item.pending} />
                        )}
                        {item.tools && item.tools.length > 0 ? (
                          <details className="tool-drawer">
                            <summary>
                              {item.tools.length} tool{item.tools.length === 1 ? "" : "s"} used
                            </summary>
                            <ul>
                              {item.tools.map((step) => (
                                <li key={step.id} className={step.error ? "bad" : ""}>
                                  <strong>{step.tool}</strong>
                                  {step.args ? <span>{step.args}</span> : null}
                                  {step.result ? (
                                    <pre>{step.result}</pre>
                                  ) : (
                                    <em>running…</em>
                                  )}
                                </li>
                              ))}
                            </ul>
                          </details>
                        ) : null}
                      </>
                    )}
                  </div>
                </article>
              );
            })
          )}
        </div>

        <form className="composer chat-composer" onSubmit={runDebate}>
          <textarea
            rows={2}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={
              runMode === "agentic"
                ? "Describe a goal for the agentic loop…"
                : runMode === "debate" || runMode === "mixed"
                  ? "Ask a decision / business question…"
                  : "Message MulteAgent…"
            }
            required
          />
          <div className="composer-row">
            <button type="button" className="btn ghost" onClick={() => setSettingsOpen(true)}>
              {selectionMeta}
            </button>
            <button type="submit" className="btn primary" disabled={running}>
              {running ? "Running…" : "Send"}
            </button>
          </div>
        </form>
      </main>

      {settingsOpen ? (
        <div className="modal-backdrop" onClick={() => setSettingsOpen(false)}>
          <div className="settings-drawer" onClick={(e) => e.stopPropagation()}>
            <header>
              <h3>Settings</h3>
              <button type="button" className="icon-btn" onClick={() => setSettingsOpen(false)}>
                ×
              </button>
            </header>

            <section>
              <div className="rail-section-head">
                <h4>Agents</h4>
                <button type="button" className="btn ghost sm" onClick={openCreate}>
                  New agent
                </button>
              </div>
              <ul className="settings-agents">
                {agents.map((agent) => (
                  <li key={agent.id}>
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={selected.has(agent.id)}
                        disabled={!agent.enabled}
                        onChange={(e) => toggleSelected(agent.id, e.target.checked)}
                      />
                      <span>
                        <strong>{agent.name}</strong>
                        <small>
                          {agent.role} · {agent.stage}
                        </small>
                      </span>
                    </label>
                    <button type="button" className="btn ghost sm" onClick={() => openEdit(agent)}>
                      Edit
                    </button>
                  </li>
                ))}
              </ul>
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
                  Restore defaults
                </button>
              ) : null}
            </section>

            <section>
              <div className="rail-section-head">
                <h4>Models</h4>
                <button type="button" className="btn ghost sm" onClick={() => refreshModels(true)}>
                  Sync
                </button>
              </div>
              <label className="check">
                <input
                  type="checkbox"
                  checked={allowOverflow}
                  onChange={(e) => setAllowOverflow(e.target.checked)}
                />
                Cloud overflow when queue waits
              </label>
              <p className="hint">
                Default route uses fast/strong/cloud roles. Override from the top bar anytime.
              </p>
            </section>
          </div>
        </div>
      ) : null}

      {modalOpen ? (
        <div className="modal-backdrop" onClick={() => setModalOpen(false)}>
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
