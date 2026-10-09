export type AgentStage = "panel" | "consensus";
export type ExecutionMode = "parallel" | "sequential";
export type RunMode = "auto" | "chat" | "debate" | "agentic" | "mixed";

export type Agent = {
  id: string;
  name: string;
  role: string;
  system_prompt: string;
  stage: AgentStage;
  enabled: boolean;
  sort_order: number;
  accent: string;
};

export type AgentInput = {
  name: string;
  role: string;
  system_prompt: string;
  stage: AgentStage;
  enabled: boolean;
  accent: string;
};

export type Health = {
  ok: boolean;
  llm_reachable: boolean;
  model: string;
  base_url: string;
  queue_depth?: number;
  estimated_wait_seconds?: number;
  llm_slots?: number;
  image_slots?: number;
  image_profile?: string;
  image_pipeline?: {
    state?: string;
    error?: string | null;
    loaded?: boolean;
    path?: string | null;
  };
  auth_required?: boolean;
};

export type AuthUser = {
  id: number;
  username: string;
  role: string;
  daily_job_quota?: number;
};

export type ModelInfo = {
  name: string;
  backend: string;
  vram_class: string;
  role: string;
  max_concurrency: number;
  enabled: boolean;
  vision?: boolean;
  image_gen?: boolean;
  available?: boolean;
};

export type ChatImage = {
  url: string;
  filename: string;
  mime: string;
};

export type Job = {
  id: string;
  user_id: number;
  mode: RunMode;
  status: string;
  priority: number;
  query: string;
  payload: Record<string, unknown>;
  model_plan: Record<string, string>;
  error?: string | null;
  log_path?: string | null;
  created_at?: string | null;
  deleted_at?: string | null;
};

export type DebateEvent = {
  type: string;
  [key: string]: unknown;
};

export type ToolStep = {
  id: string;
  tool: string;
  args?: string;
  result?: string;
  error?: boolean;
};

export type JobArtifact = {
  filename: string;
  mime?: string;
  relpath?: string;
  bytes?: number;
  kind?: string;
  jobId?: string;
};

export type FeedItem = {
  id: string;
  kind: "user" | "agent" | "system";
  title: string;
  meta?: string;
  body: string;
  accent?: string;
  pending?: boolean;
  agentId?: string;
  /** Collapsed tool activity attached to an agent step */
  tools?: ToolStep[];
  images?: ChatImage[];
  jobId?: string;
  imageFiles?: { filename: string; mime?: string }[];
  artifacts?: JobArtifact[];
};
