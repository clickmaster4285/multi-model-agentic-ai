/** Client-side chat threads so one chat can hold many jobs/messages. */

import { newId } from "@/lib/id";

export type ChatThread = {
  id: string;
  title: string;
  jobIds: string[];
  projectId: string | null;
  pinned: boolean;
  /** ISO timestamp when soft-deleted; null/undefined = active */
  deletedAt: string | null;
  updatedAt: string;
};

const KEY = "multeagent_threads_v1";
const ACTIVE_KEY = "multeagent_active_thread";

function normalize(raw: Partial<ChatThread> & { id: string }): ChatThread {
  return {
    id: raw.id,
    title: (raw.title || "Chat").trim() || "Chat",
    jobIds: Array.isArray(raw.jobIds) ? raw.jobIds : [],
    projectId: raw.projectId ?? null,
    pinned: Boolean(raw.pinned),
    deletedAt: raw.deletedAt ?? null,
    updatedAt: raw.updatedAt || new Date().toISOString(),
  };
}

function read(): ChatThread[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((t): t is Partial<ChatThread> & { id: string } => Boolean(t && typeof t === "object" && "id" in t))
      .map(normalize);
  } catch {
    return [];
  }
}

function write(threads: ChatThread[]) {
  localStorage.setItem(KEY, JSON.stringify(threads));
}

function sortThreads(threads: ChatThread[]): ChatThread[] {
  return [...threads].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return b.updatedAt.localeCompare(a.updatedAt);
  });
}

/** Active (non-deleted) threads for the sidebar. */
export function listThreads(): ChatThread[] {
  return sortThreads(read().filter((t) => !t.deletedAt));
}

/** All threads including soft-deleted (for migrations / restore). */
export function listAllThreads(): ChatThread[] {
  return sortThreads(read());
}

export function getThread(id: string, includeDeleted = false): ChatThread | null {
  const t = read().find((x) => x.id === id) || null;
  if (!t) return null;
  if (!includeDeleted && t.deletedAt) return null;
  return t;
}

export function createThread(title = "New chat", projectId: string | null = null): ChatThread {
  const thread: ChatThread = {
    id: newId(),
    title: title.trim() || "New chat",
    jobIds: [],
    projectId,
    pinned: false,
    deletedAt: null,
    updatedAt: new Date().toISOString(),
  };
  const all = read();
  all.unshift(thread);
  write(all);
  localStorage.setItem(ACTIVE_KEY, thread.id);
  return thread;
}

export function renameThread(id: string, title: string): ChatThread[] {
  const all = read().map((t) =>
    t.id === id
      ? { ...t, title: title.trim() || t.title, updatedAt: new Date().toISOString() }
      : t,
  );
  write(all);
  return listThreads();
}

export function pinThread(id: string, pinned?: boolean): ChatThread[] {
  const all = read().map((t) => {
    if (t.id !== id || t.deletedAt) return t;
    return { ...t, pinned: pinned ?? !t.pinned, updatedAt: new Date().toISOString() };
  });
  write(all);
  return listThreads();
}

export function addJobToThread(threadId: string, jobId: string, title?: string): ChatThread[] {
  const all = read().map((t) => {
    if (t.id !== threadId || t.deletedAt) return t;
    const jobIds = t.jobIds.includes(jobId) ? t.jobIds : [...t.jobIds, jobId];
    return {
      ...t,
      jobIds,
      title: t.title === "New chat" && title ? title : t.title,
      updatedAt: new Date().toISOString(),
    };
  });
  write(all);
  return listThreads();
}

/** Soft-delete a conversation (keeps record + job ids for audit). */
export function softDeleteThread(id: string): ChatThread[] {
  const now = new Date().toISOString();
  const all = read().map((t) =>
    t.id === id ? { ...t, deletedAt: t.deletedAt || now, pinned: false, updatedAt: now } : t,
  );
  write(all);
  if (localStorage.getItem(ACTIVE_KEY) === id) localStorage.removeItem(ACTIVE_KEY);
  return listThreads();
}

/** @deprecated use softDeleteThread */
export function deleteThread(id: string): ChatThread[] {
  return softDeleteThread(id);
}

export function getActiveThreadId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(ACTIVE_KEY);
}

export function setActiveThreadId(id: string | null) {
  if (typeof window === "undefined") return;
  if (id) localStorage.setItem(ACTIVE_KEY, id);
  else localStorage.removeItem(ACTIVE_KEY);
}

/**
 * Ensure every known (non-deleted) job appears in some active thread.
 * Skips jobs already on any thread (including soft-deleted) so they don't reappear.
 */
export function ensureThreadsForJobs(
  jobs: { id: string; query: string; created_at?: string | null; deleted_at?: string | null }[],
): ChatThread[] {
  const threads = read();
  const seen = new Set(threads.flatMap((t) => t.jobIds));
  let changed = false;
  for (const job of jobs) {
    if (job.deleted_at) continue;
    if (seen.has(job.id)) continue;
    threads.push({
      id: newId(),
      title: job.query.trim().slice(0, 42) || "Chat",
      jobIds: [job.id],
      projectId: null,
      pinned: false,
      deletedAt: null,
      updatedAt: job.created_at || new Date().toISOString(),
    });
    seen.add(job.id);
    changed = true;
  }
  if (changed) write(threads);
  return listThreads();
}
