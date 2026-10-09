"use client";

import {
  FormEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ClipboardEvent,
  type DragEvent,
  type MouseEvent,
} from "react";
import {
  getApiOrigin,
  cancelJob,
  createAgent,
  createJob,
  deleteAgent,
  fetchJobAttachment,
  getHealth,
  type ImageForceIntent,
  type ImageProfile,
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
  ChatImage,
} from "@/lib/types";
import MarkdownBody from "@/components/MarkdownBody";
import MessageAvatar from "@/components/MessageAvatar";
import {
  appendUserMessage,
  buildFeedFromEvents,
  mergeArtifactsFromJob,
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
import { newId } from "@/lib/id";

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

type PendingImage = {
  id: string;
  file: File;
  url: string;
  mime: string;
};

const MAX_CHAT_IMAGES = 4;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(new Error("Could not read image"));
    reader.readAsDataURL(file);
  });
}

async function chatImagesToPending(images?: ChatImage[]): Promise<PendingImage[]> {
  if (!images?.length) return [];
  const out: PendingImage[] = [];
  for (const img of images) {
    try {
      const res = await fetch(img.url);
      if (!res.ok) continue;
      const blob = await res.blob();
      const file = new File([blob], img.filename || "image.png", {
        type: img.mime || blob.type || "image/png",
      });
      out.push({
        id: newId(),
        file,
        url: URL.createObjectURL(file),
        mime: file.type,
      });
    } catch {
      // skip images we cannot reload
    }
  }
  return out;
}

function IconPaperclip() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M21.44 11.05 12.25 20.24a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"
        stroke="currentColor"
        strokeWidth="1.85"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconSend() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M3.2 21.4 22 12 3.2 2.6 3 10.1 15 12 3 13.9z" />
    </svg>
  );
}

async function attachmentsFromJob(job: Job): Promise<ChatImage[]> {
  const raw = job.payload?.attachments;
  if (!Array.isArray(raw) || raw.length === 0) return [];
  const out: ChatImage[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const kind = String((item as { kind?: string }).kind || "user");
    if (kind === "generated") continue;
    const filename = String((item as { filename?: string }).filename || "");
    if (!filename) continue;
    const mime = String((item as { mime?: string }).mime || "");
    const isImage =
      mime.startsWith("image/") ||
      /\.(png|jpe?g|webp|gif)$/i.test(filename);
    if (!isImage) continue;
    try {
      const url = await fetchJobAttachment(job.id, filename);
      out.push({
        url,
        filename,
        mime: mime || "image/jpeg",
      });
    } catch {
      // skip missing files
    }
  }
  return out;
}

function AgentThumbs({
  item,
  fallbackJobId,
}: {
  item: FeedItem;
  fallbackJobId: string | null;
}) {
  const jobId = item.jobId || fallbackJobId;
  const [imgs, setImgs] = useState<ChatImage[]>(item.images || []);
  useEffect(() => {
    if (item.images && item.images.length > 0) {
      setImgs(item.images);
      return;
    }
    const files = item.imageFiles;
    if (!files?.length || !jobId) return;
    let cancelled = false;
    (async () => {
      const out: ChatImage[] = [];
      for (const file of files) {
        if (!file.filename) continue;
        try {
          const url = await fetchJobAttachment(jobId, file.filename);
          out.push({
            url,
            filename: file.filename,
            mime: file.mime || "image/png",
          });
        } catch {
          // skip
        }
      }
      if (!cancelled) setImgs(out);
    })();
    return () => {
      cancelled = true;
    };
  }, [jobId, item.images, item.imageFiles]);
  if (!imgs.length) return null;
  return (
    <div className="chat-thumbs chat-thumbs-generated">
      {imgs.map((img) => (
        <a key={img.url} href={img.url} target="_blank" rel="noreferrer">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={img.url} alt={img.filename} />
        </a>
      ))}
    </div>
  );
}

function ArtifactChips({
  item,
  fallbackJobId,
}: {
  item: FeedItem;
  fallbackJobId: string | null;
}) {
  const jobId = item.jobId || fallbackJobId;
  const arts = item.artifacts || [];
  if (!arts.length || !jobId) return null;
  return (
    <div className="artifact-chips">
      {arts.map((art) => (
        <button
          key={art.filename}
          type="button"
          className="artifact-chip"
          title={`Download ${art.filename}`}
          onClick={() => {
            void (async () => {
              try {
                const url = await fetchJobAttachment(jobId, art.filename);
                const a = document.createElement("a");
                a.href = url;
                a.download = art.filename;
                a.click();
                window.setTimeout(() => URL.revokeObjectURL(url), 1500);
              } catch (err) {
                alert(err instanceof Error ? err.message : String(err));
              }
            })();
          }}
        >
          ↓ {art.filename}
          {art.bytes ? ` · ${Math.max(1, Math.round(art.bytes / 1024))} KB` : ""}
        </button>
      ))}
    </div>
  );
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
  const [pendingImages, setPendingImages] = useState<PendingImage[]>([]);
  const [imageProfile, setImageProfile] = useState<ImageProfile>("fast");
  const [editStrength, setEditStrength] = useState<"close" | "medium" | "restyle">("medium");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const queryRef = useRef<HTMLTextAreaElement | null>(null);
  const [editingFromId, setEditingFromId] = useState<string | null>(null);
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
    const data = await listModels(refresh);
    setModels(data);
    const live = new Set(
      data.filter((m) => m.enabled && m.available !== false).map((m) => m.name),
    );
    setModelOverride((prev) => (prev && !live.has(prev) ? "" : prev));
    return data;
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
            const images = await attachmentsFromJob(history.job);
            const slice = mergeArtifactsFromJob(
              buildFeedFromEvents(history.events, history.job.query, images).filter(
                (item) => item.id !== STATUS_ID,
              ),
              history.job,
            );
            built = [
              ...built,
              ...slice.map((item) =>
                item.kind === "user"
                  ? { ...item, meta: `${history.job.mode} · ${history.job.status}` }
                  : { ...item, jobId: item.jobId || history.job.id },
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
    const timer = setInterval(() => {
      void refreshHealth();
      if (getToken()) void refreshModels(false);
    }, 15000);
    return () => clearInterval(timer);
  }, [refreshHealth, refreshModels]);

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

  const resizeComposer = useCallback(() => {
    const el = queryRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, 256);
    el.style.height = `${Math.max(next, 35)}px`;
  }, []);

  useEffect(() => {
    requestAnimationFrame(resizeComposer);
  }, [query, resizeComposer]);

  function startNewChat() {
    const thread = createThread("New chat", projectId);
    setThreads(listThreads());
    selectThreadId(thread.id);
    setFeed([]);
    setQuery("");
    setPendingImages([]);
    setEditingFromId(null);
    setActiveJobId(null);
    setChatTitle("New chat");
    setRunning(false);
  }

  function cancelComposerEdit() {
    setEditingFromId(null);
    setQuery("");
    setPendingImages((prev) => {
      prev.forEach((img) => URL.revokeObjectURL(img.url));
      return [];
    });
    requestAnimationFrame(resizeComposer);
  }

  async function beginEdit(item: FeedItem) {
    setEditingFromId(item.id);
    setQuery(item.body === "What's in this image?" ? "" : item.body);
    setPendingImages((prev) => {
      prev.forEach((img) => URL.revokeObjectURL(img.url));
      return [];
    });
    const reloaded = await chatImagesToPending(item.images);
    setPendingImages(reloaded);
    queryRef.current?.focus();
    requestAnimationFrame(resizeComposer);
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

  function addImageFiles(files: File[]) {
    const accepted: PendingImage[] = [];
    for (const file of files) {
      if (!file.type.startsWith("image/")) continue;
      if (file.size > MAX_IMAGE_BYTES) {
        alert(`“${file.name}” is over 5MB.`);
        continue;
      }
      accepted.push({
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        file,
        url: URL.createObjectURL(file),
        mime: file.type || "image/png",
      });
    }
    if (accepted.length === 0) return;
    setPendingImages((prev) => {
      const next = [...prev, ...accepted];
      if (next.length > MAX_CHAT_IMAGES) {
        next.slice(MAX_CHAT_IMAGES).forEach((img) => URL.revokeObjectURL(img.url));
        return next.slice(0, MAX_CHAT_IMAGES);
      }
      return next;
    });
  }

  function removePendingImage(id: string) {
    setPendingImages((prev) => {
      const hit = prev.find((img) => img.id === id);
      if (hit) URL.revokeObjectURL(hit.url);
      return prev.filter((img) => img.id !== id);
    });
  }

  function onComposerPaste(event: ClipboardEvent<HTMLTextAreaElement>) {
    const files = [...(event.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
    if (files.length === 0) return;
    event.preventDefault();
    addImageFiles(files);
  }

  function onComposerDrop(event: DragEvent<HTMLFormElement>) {
    event.preventDefault();
    const files = [...(event.dataTransfer?.files || [])].filter((f) => f.type.startsWith("image/"));
    if (files.length) addImageFiles(files);
  }

  function strengthValue(): number {
    if (editStrength === "close") return 0.28;
    if (editStrength === "restyle") return 0.55;
    return 0.4;
  }

  async function submitMessage(
    text: string,
    images: PendingImage[],
    opts: { retry?: boolean; forceIntent?: ImageForceIntent } = {},
  ) {
    if (running) return;
    const trimmed = text.trim();
    if (!trimmed && images.length === 0) return;
    if (opts.forceIntent === "describe" && images.length === 0) {
      alert("Attach an image to Describe.");
      return;
    }
    if (opts.forceIntent === "generate" && !trimmed) {
      alert("Type a prompt for Generate.");
      return;
    }
    if (
      (opts.forceIntent === "edit" || opts.forceIntent === "inpaint") &&
      images.length === 0
    ) {
      alert("Attach an image to Edit / Inpaint.");
      return;
    }
    if (runMode === "debate" || runMode === "mixed") {
      if (selected.size === 0) {
        alert("Select at least one agent in Settings.");
        setSettingsOpen(true);
        return;
      }
    }

    let threadId = activeThreadId;
    const titleSeed = trimmed || images[0]?.file.name || "Image";
    if (!threadId) {
      const thread = createThread(titleFromQuery(titleSeed), projectId);
      threadId = thread.id;
      selectThreadId(thread.id);
    }

    const snapshot = images;
    const chatImages: ChatImage[] = snapshot.map((img) => ({
      url: img.url,
      filename: img.file.name,
      mime: img.mime,
    }));
    setRunning(true);
    setEditingFromId(null);
    setChatTitle(titleFromQuery(titleSeed));
    if (opts.retry) {
      setFeed((prev) => upsertStatus(prev, "Regenerating", "Sending the last query again…"));
    } else {
      setFeed((prev) =>
        appendUserMessage(
          prev,
          trimmed || "What's in this image?",
          `${runMode} · ${parallel ? "parallel" : "sequential"}`,
          chatImages,
        ),
      );
    }
    setQuery("");
    setPendingImages([]);

    try {
      const payloadImages = await Promise.all(
        snapshot.map(async (img) => ({
          filename: img.file.name,
          mime: img.mime,
          data: await fileToBase64(img.file),
        })),
      );
      const force = opts.forceIntent;
      const job = await createJob({
        query: trimmed || (force === "describe" ? "What's in this image?" : ""),
        mode: force ? "chat" : runMode,
        execution_mode: parallel ? "parallel" : "sequential",
        agent_ids: [...selected],
        model: modelOverride || undefined,
        allow_overflow: allowOverflow,
        images: payloadImages,
        force_intent: force,
        image_profile: imageProfile,
        image_strength:
          force === "edit" || force === "inpaint" ? strengthValue() : undefined,
      });
      setActiveJobId(job.id);
      setThreads(addJobToThread(threadId, job.id, titleFromQuery(titleSeed)));
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

  async function runDebate(event: FormEvent) {
    event.preventDefault();
    await submitMessage(query.trim(), pendingImages);
  }

  function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Escape" && editingFromId) {
      event.preventDefault();
      cancelComposerEdit();
      return;
    }
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void submitMessage(query.trim(), pendingImages);
  }

  async function retryLast() {
    const lastUser = [...feed].reverse().find((item) => item.kind === "user");
    if (!lastUser || running) return;
    const images = await chatImagesToPending(lastUser.images);
    await submitMessage(
      lastUser.body === "What's in this image?" ? "" : lastUser.body,
      images,
      { retry: true },
    );
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
  const lastUserIdx = feed.reduce((acc, item, i) => (item.kind === "user" ? i : acc), -1);
  const lastAgentIdx = feed.reduce((acc, item, i) => (item.kind === "agent" ? i : acc), -1);

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
          <div className="rail-section grow">
            <div className="rail-section-head">
              <h2>Projects</h2>
              <button type="button" className="btn ghost sm" onClick={handleNewProject}>
                New
              </button>
            </div>
            <ul className="history-list">
              <li>
                <button
                  type="button"
                  className={`nav-item ${projectId ? "" : "active"}`}
                  onClick={() => selectProject(null)}
                >
                  All chats
                </button>
              </li>
              {projects.map((p) => (
                <li key={p.id}>
                  <div className={`nav-item row ${projectId === p.id ? "active" : ""}`}>
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
                </li>
              ))}
            </ul>
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
                .filter((m) => m.enabled && m.available !== false)
                .map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.image_gen ? `${m.name} · image gen` : m.vision ? `${m.name} · vision` : m.name}
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
                feed[index - 1].body.trim() === item.body.trim() &&
                !(item.images && item.images.length > 0)
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
                          <div className="bubble-body">
                            {item.images && item.images.length > 0 ? (
                              <div className="chat-thumbs">
                                {item.images.map((img) => (
                                  <a key={img.url} href={img.url} target="_blank" rel="noreferrer">
                                    {/* eslint-disable-next-line @next/next/no-img-element */}
                                    <img src={img.url} alt={img.filename} />
                                  </a>
                                ))}
                              </div>
                            ) : null}
                            {item.body}
                          </div>
                        ) : (
                          <>
                            <AgentThumbs item={item} fallbackJobId={activeJobId} />
                            <ArtifactChips item={item} fallbackJobId={activeJobId} />
                            <MarkdownBody content={item.body} pending={item.pending} />
                          </>
                        )}
                        {item.kind === "user" ||
                        index === lastAgentIdx ||
                        (isError && index === feed.length - 1) ? (
                          <div className="msg-toolbar">
                            {item.kind === "user" ? (
                              <button
                                type="button"
                                className="msg-action"
                                disabled={running}
                                onClick={() => void beginEdit(item)}
                              >
                                Edit
                              </button>
                            ) : null}
                            {(index === lastUserIdx ||
                              index === lastAgentIdx ||
                              (isError && index === feed.length - 1)) &&
                            !running ? (
                              <button type="button" className="msg-action" onClick={() => void retryLast()}>
                                Retry
                              </button>
                            ) : null}
                          </div>
                        ) : null}
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

        <form
          className="composer chat-composer"
          onSubmit={runDebate}
          onDragOver={(e) => e.preventDefault()}
          onDrop={onComposerDrop}
        >
          {editingFromId ? (
            <div className="composer-editing">
              <span>Editing previous message</span>
              <button type="button" className="msg-action" onClick={cancelComposerEdit}>
                Cancel
              </button>
            </div>
          ) : null}
          {pendingImages.length > 0 ? (
            <div className="pending-thumbs">
              {pendingImages.map((img) => (
                <div key={img.id} className="pending-thumb">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={img.url} alt={img.file.name} />
                  <button
                    type="button"
                    className="icon-btn danger"
                    title="Remove image"
                    onClick={() => removePendingImage(img.id)}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          ) : null}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            multiple
            hidden
            onChange={(e) => {
              addImageFiles([...(e.target.files || [])]);
              e.target.value = "";
            }}
          />
          <div className="composer-bar">
            <button
              type="button"
              className="composer-icon"
              title="Attach image"
              aria-label="Attach image"
              onClick={() => fileInputRef.current?.click()}
            >
              <IconPaperclip />
            </button>
            <textarea
              ref={queryRef}
              rows={1}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                requestAnimationFrame(resizeComposer);
              }}
              onInput={resizeComposer}
              onPaste={onComposerPaste}
              onKeyDown={onComposerKeyDown}
              placeholder={
                pendingImages.length
                  ? "Ask about the image… (optional)"
                  : runMode === "agentic"
                    ? "Describe a goal for the agentic loop…"
                    : runMode === "debate" || runMode === "mixed"
                      ? "Ask a decision / business question…"
                      : "Message MulteAgent — paste or attach an image"
              }
              required={pendingImages.length === 0}
            />
            <button
              type="submit"
              className="composer-send"
              disabled={running}
              title={running ? "Running…" : "Send (Enter)"}
              aria-label={running ? "Running" : "Send"}
            >
              <IconSend />
            </button>
          </div>
          <div className="composer-row image-actions">
            <button type="button" className="btn ghost sm" onClick={() => setSettingsOpen(true)}>
              {selectionMeta}
            </button>
            <label className="image-preset">
              Profile
              <select
                value={imageProfile}
                onChange={(e) => setImageProfile(e.target.value as ImageProfile)}
              >
                <option value="fast">Fast</option>
                <option value="balanced">Balanced</option>
                <option value="quality">Quality</option>
              </select>
            </label>
            <label className="image-preset">
              Edit strength
              <select
                value={editStrength}
                onChange={(e) =>
                  setEditStrength(e.target.value as "close" | "medium" | "restyle")
                }
              >
                <option value="close">Keep close</option>
                <option value="medium">Medium</option>
                <option value="restyle">Restyle</option>
              </select>
            </label>
            <button
              type="button"
              className="btn ghost sm"
              disabled={running || pendingImages.length === 0}
              title="Vision: describe the attached image (needs Ollama)"
              onClick={() => void submitMessage(query.trim(), pendingImages, { forceIntent: "describe" })}
            >
              Describe
            </button>
            <button
              type="button"
              className="btn ghost sm"
              disabled={running}
              title="Force local SDXL text-to-image"
              onClick={() => void submitMessage(query.trim(), pendingImages, { forceIntent: "generate" })}
            >
              Generate
            </button>
            <button
              type="button"
              className="btn ghost sm"
              disabled={running || pendingImages.length === 0}
              title="Force img2img from the attached image"
              onClick={() => void submitMessage(query.trim(), pendingImages, { forceIntent: "edit" })}
            >
              Edit
            </button>
            <button
              type="button"
              className="btn ghost sm"
              disabled={running || pendingImages.length === 0}
              title="Inpaint: attach source + a mask_* image (white = repaint)"
              onClick={() => void submitMessage(query.trim(), pendingImages, { forceIntent: "inpaint" })}
            >
              Inpaint
            </button>
            <button
              type="button"
              className="btn ghost sm"
              disabled={running || !query.trim()}
              title="Create downloadable Word/PDF/PPT/HTML/Excel via agentic tools"
              onClick={() =>
                void submitMessage(query.trim(), pendingImages, { forceIntent: "doc_gen" })
              }
            >
              Document
            </button>
            {health?.image_pipeline?.state ? (
              <span className="image-ready" title={health.image_pipeline.error || ""}>
                Image model: {health.image_pipeline.state}
              </span>
            ) : null}
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
