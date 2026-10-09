import type {
  Agent,
  AgentInput,
  AuthUser,
  DebateEvent,
  ExecutionMode,
  Health,
  Job,
  ModelInfo,
  RunMode,
} from "./types";

const API_PORT = process.env.NEXT_PUBLIC_API_PORT || "8787";

function configuredOrigins(): string[] {
  const raw =
    process.env.NEXT_PUBLIC_API_ORIGINS ||
    process.env.NEXT_PUBLIC_API_ORIGIN ||
    `http://127.0.0.1:${API_PORT}`;
  return raw
    .split(",")
    .map((s) => s.trim().replace(/\/$/, ""))
    .filter(Boolean);
}

/** Resolve API base from env list using the current page hostname (LAN vs localhost). */
export function getApiOrigin(): string {
  const origins = configuredOrigins();
  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    const match = origins.find((origin) => {
      try {
        return new URL(origin).hostname === host;
      } catch {
        return false;
      }
    });
    if (match) return match;
    if (host === "localhost" || host === "127.0.0.1" || /^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)) {
      const proto = window.location.protocol === "https:" ? "https:" : "http:";
      return `${proto}//${host}:${API_PORT}`;
    }
  }
  return origins[0] || `http://127.0.0.1:${API_PORT}`;
}

/** @deprecated use getApiOrigin() — kept for display fallbacks during SSR */
export const API_ORIGIN = configuredOrigins()[0] || `http://127.0.0.1:${API_PORT}`;

const TOKEN_KEY = "multeagent_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

function apiUrl(path: string): string {
  return `${getApiOrigin()}${path.startsWith("/") ? path : `/${path}`}`;
}

function authHeaders(json = true): HeadersInit {
  const headers: Record<string, string> = {};
  if (json) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function readError(response: Response): Promise<string> {
  try {
    const data = await response.json();
    if (typeof data.detail === "string") return data.detail;
    return JSON.stringify(data.detail ?? data);
  } catch {
    return response.statusText || `HTTP ${response.status}`;
  }
}

function networkHint(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (/failed to fetch|networkerror|network error|load failed/i.test(msg)) {
    return `Cannot reach API at ${getApiOrigin()}. Start: python main.py --gui (${msg})`;
  }
  return msg;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  try {
    const response = await fetch(apiUrl(path), {
      ...init,
      headers: { ...authHeaders(!(init?.body instanceof FormData)), ...(init?.headers || {}) },
    });
    if (!response.ok) {
      // Drop stale session so the login screen returns instead of a half-authed UI.
      if (response.status === 401 && !path.includes("/api/auth/login")) {
        setToken(null);
      }
      throw new Error(await readError(response));
    }
    if (response.status === 204) return undefined as T;
    return response.json();
  } catch (err) {
    throw new Error(networkHint(err));
  }
}

export async function login(username: string, password: string) {
  const data = await request<{
    access_token: string;
    user: AuthUser;
  }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setToken(data.access_token);
  return data.user;
}

export async function me(): Promise<AuthUser> {
  return request("/api/auth/me");
}

export async function getHealth(): Promise<Health> {
  return request("/api/health");
}

export async function listAgents(): Promise<Agent[]> {
  return request("/api/agents");
}

export async function createAgent(payload: AgentInput): Promise<Agent> {
  return request("/api/agents", { method: "POST", body: JSON.stringify(payload) });
}

export async function updateAgent(id: string, payload: Partial<AgentInput>): Promise<Agent> {
  return request(`/api/agents/${encodeURIComponent(id)}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export async function deleteAgent(id: string): Promise<void> {
  await request(`/api/agents/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export async function resetAgents(): Promise<Agent[]> {
  return request("/api/agents/reset", { method: "POST", body: "{}" });
}

export async function listModels(refresh = false): Promise<ModelInfo[]> {
  return request(`/api/models${refresh ? "?refresh=true" : ""}`);
}

export type ImageForceIntent = "describe" | "generate" | "edit" | "inpaint";
export type ImageProfile = "fast" | "quality" | "balanced";

export async function createJob(payload: {
  query: string;
  mode: RunMode;
  execution_mode: ExecutionMode;
  agent_ids: string[];
  model?: string;
  allow_overflow?: boolean;
  images?: { filename: string; mime: string; data: string }[];
  force_intent?: ImageForceIntent;
  image_profile?: ImageProfile;
  image_strength?: number;
}): Promise<Job> {
  return request("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
}

export async function listJobs(limit = 100): Promise<Job[]> {
  return request(`/api/jobs?limit=${Math.min(Math.max(limit, 1), 100)}`);
}

export async function cancelJob(jobId: string): Promise<Job> {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    body: "{}",
  });
}

/** Soft-delete jobs (conversation turns). Rows are kept with deleted_at set. */
export async function softDeleteJobs(jobIds: string[]): Promise<{
  status: string;
  deleted_ids: string[];
  count: number;
}> {
  return request("/api/jobs/soft-delete", {
    method: "POST",
    body: JSON.stringify({ job_ids: jobIds }),
  });
}

export async function fetchJobAttachment(jobId: string, filename: string): Promise<string> {
  const response = await fetch(
    apiUrl(`/api/jobs/${encodeURIComponent(jobId)}/attachments/${encodeURIComponent(filename)}`),
    { headers: authHeaders(false) },
  );
  if (!response.ok) throw new Error(await readError(response));
  const blob = await response.blob();
  return URL.createObjectURL(blob);
}

export async function getJobHistory(jobId: string): Promise<{
  job: Job;
  events: DebateEvent[];
}> {
  return request(`/api/jobs/${encodeURIComponent(jobId)}/history`);
}

export async function streamJobEvents(
  jobId: string,
  onEvent: (event: DebateEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(apiUrl(`/api/jobs/${encodeURIComponent(jobId)}/events`), {
      headers: authHeaders(false),
      signal,
    });
  } catch (err) {
    throw new Error(networkHint(err));
  }
  if (!response.ok) throw new Error(await readError(response));
  if (!response.body) throw new Error("Empty event stream");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split("\n\n");
      buffer = chunks.pop() || "";
      for (const chunk of chunks) {
        const data = chunk
          .split("\n")
          .filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trim())
          .join("");
        if (!data) continue;
        onEvent(JSON.parse(data) as DebateEvent);
      }
    }
  } catch (err) {
    throw new Error(networkHint(err));
  }
}
