import type { ChatImage, DebateEvent, FeedItem, Job, JobArtifact, ToolStep } from "@/lib/types";
import { formatAgentOutput, formatToolResult, summarizeArgs } from "@/lib/format";

const DOC_KINDS = new Set([
  "document",
  "html",
  "docx",
  "pdf",
  "pptx",
  "xlsx",
  "zip",
]);

/** Document downloads stored on job.payload.attachments (not chat images). */
export function artifactsFromJobPayload(job: Job): JobArtifact[] {
  const raw = job.payload?.attachments;
  if (!Array.isArray(raw)) return [];
  const out: JobArtifact[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const filename = String(row.filename || "");
    if (!filename) continue;
    const kind = String(row.kind || "");
    const mime = String(row.mime || "");
    const isDoc =
      DOC_KINDS.has(kind) ||
      /\.(pdf|docx|pptx|xlsx|html?|zip|md|txt|csv)$/i.test(filename) ||
      (mime.startsWith("application/") && !mime.includes("octet-stream")) ||
      mime === "text/html" ||
      mime === "text/markdown";
    if (!isDoc) continue;
    out.push({
      filename,
      mime: mime || undefined,
      relpath: row.relpath ? String(row.relpath) : undefined,
      bytes: typeof row.bytes === "number" ? row.bytes : undefined,
      kind: kind || "document",
      jobId: job.id,
    });
  }
  return out;
}

/** Attach payload artifacts to the last agent bubble when SSE events were missing. */
export function mergeArtifactsFromJob(feed: FeedItem[], job: Job): FeedItem[] {
  const arts = artifactsFromJobPayload(job);
  if (!arts.length) return feed;
  const next = [...feed];
  for (let i = next.length - 1; i >= 0; i -= 1) {
    if (next[i].kind !== "agent") continue;
    const existing = next[i].artifacts || [];
    const seen = new Set(existing.map((a) => a.filename));
    const merged = [...existing];
    for (const art of arts) {
      if (seen.has(art.filename)) continue;
      seen.add(art.filename);
      merged.push(art);
    }
    next[i] = {
      ...next[i],
      artifacts: merged,
      jobId: next[i].jobId || job.id,
    };
    return next;
  }
  // No agent bubble — add a lightweight downloads row
  return [
    ...next,
    {
      id: uid(),
      kind: "agent",
      title: "Downloads",
      meta: "from job",
      body: "Files from this run",
      accent: "#3aa89a",
      pending: false,
      jobId: job.id,
      artifacts: arts,
    },
  ];
}

export const STATUS_ID = "live-status";

function uid() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function upsertStatus(
  prev: FeedItem[],
  title: string,
  body: string,
  error = false,
): FeedItem[] {
  const next: FeedItem = {
    id: STATUS_ID,
    kind: "system",
    title,
    body,
    meta: error ? "error" : undefined,
  };
  const idx = prev.findIndex((item) => item.id === STATUS_ID);
  if (idx === -1) return [...prev, next];
  const copy = [...prev];
  copy[idx] = next;
  return copy;
}

function attachToolCall(prev: FeedItem[], event: DebateEvent): FeedItem[] {
  const agentId = String(event.agent_id || "");
  const tool = String(event.tool || "tool");
  const step: ToolStep = {
    id: uid(),
    tool,
    args: summarizeArgs(event.args),
  };
  let attached = false;
  const mapped = prev.map((item) => {
    if (!item.pending || item.agentId !== agentId) return item;
    attached = true;
    return { ...item, body: `Using ${tool}…`, tools: [...(item.tools || []), step] };
  });
  if (attached) return mapped;
  return upsertStatus(prev, "Working", `Using ${tool}…`);
}

function attachToolResult(prev: FeedItem[], event: DebateEvent): FeedItem[] {
  const agentId = String(event.agent_id || "");
  const tool = String(event.tool || "tool");
  const result = formatToolResult(String(event.result || ""));
  const isError = result.startsWith("Error:");
  let updated = false;
  const mapped = prev.map((item) => {
    if (item.agentId !== agentId || !item.tools?.length) return item;
    const tools = [...item.tools];
    for (let i = tools.length - 1; i >= 0; i -= 1) {
      if (tools[i].tool === tool && tools[i].result == null) {
        tools[i] = { ...tools[i], result, error: isError };
        updated = true;
        break;
      }
    }
    if (!updated) return item;
    return {
      ...item,
      body: isError ? `Tool issue · ${tool}` : `Got results from ${tool}`,
      tools,
    };
  });
  if (updated) return mapped;
  return upsertStatus(prev, isError ? "Tool issue" : "Tool", `${tool} finished`, isError);
}

/** Pure reducer: apply one backend event onto a feed snapshot. */
export function reduceFeed(prev: FeedItem[], event: DebateEvent): FeedItem[] {
  const type = String(event.type || "");

  if (type === "job_queued") {
    return upsertStatus(
      prev,
      "Queued",
      `~${Number(event.estimated_wait_seconds || 0).toFixed(0)}s wait`,
    );
  }
  if (type === "route_decided") {
    const requested = String(event.requested_mode || "auto");
    const resolved = String(event.resolved_mode || event.intent || "chat");
    const model = event.model ? String(event.model) : "";
    const intent = String(event.intent || resolved);
    const reason = String(event.reason || "").slice(0, 80);
    const label =
      intent === "vision"
        ? `Auto -> vision${model ? ` · ${model}` : ""}`
        : requested === "auto" || requested === resolved
          ? `Auto -> ${resolved}`
          : `${requested} (forced)`;
    return upsertStatus(
      prev,
      "Route",
      reason ? `${label} · ${intent} · ${reason}` : `${label} · ${intent}`,
    );
  }
  if (type === "job_started" || type === "session_start" || type === "agentic_start") {
    return upsertStatus(prev, "Working", "Running…");
  }
  if (type === "agent_start") {
    return [
      ...prev.filter((item) => item.id !== STATUS_ID),
      {
        id: uid(),
        kind: "agent",
        title: String(event.name || "Agent"),
        meta: String(event.role || "thinking"),
        body: "Working…",
        accent: String(event.accent || "#3aa89a"),
        pending: true,
        agentId: String(event.agent_id || ""),
        tools: [],
      },
    ];
  }
  if (type === "tool_call") return attachToolCall(prev, event);
  if (type === "tool_result") return attachToolResult(prev, event);
  if (type === "artifact_ready") {
    const filename = String(event.filename || "");
    if (!filename) return prev;
    const art = {
      filename,
      mime: event.mime ? String(event.mime) : undefined,
      relpath: event.relpath ? String(event.relpath) : undefined,
      bytes: typeof event.bytes === "number" ? event.bytes : undefined,
      kind: event.kind ? String(event.kind) : "document",
      jobId: event.job_id ? String(event.job_id) : undefined,
    };
    const next = [...prev];
    let attached = false;
    for (let i = next.length - 1; i >= 0; i -= 1) {
      if (next[i].kind !== "agent") continue;
      const artifacts = [...(next[i].artifacts || [])].filter((a) => a.filename !== filename);
      artifacts.push(art);
      next[i] = {
        ...next[i],
        artifacts,
        jobId: next[i].jobId || art.jobId,
        body: next[i].pending ? `Created ${filename}…` : next[i].body,
      };
      attached = true;
      break;
    }
    if (!attached) {
      next.push({
        id: uid(),
        kind: "agent",
        title: "Downloads",
        meta: "file ready",
        body: `Created ${filename}`,
        accent: "#3aa89a",
        pending: false,
        jobId: art.jobId,
        artifacts: [art],
      });
    }
    return upsertStatus(next, "File ready", `${filename} — download below`);
  }
  if (type === "agent_done") {
    const rawOut = event.output;
    const asText =
      typeof rawOut === "string"
        ? rawOut
        : rawOut == null
          ? ""
          : JSON.stringify(rawOut, null, 2);
    const body = formatAgentOutput(asText);
    const imageFiles = Array.isArray(event.images)
      ? (event.images as { filename?: string; mime?: string }[])
          .filter((img) => img && img.filename)
          .map((img) => ({ filename: String(img.filename), mime: img.mime }))
      : undefined;
    const jobId = event.job_id ? String(event.job_id) : undefined;
    const hasPending = prev.some((item) => item.agentId === event.agent_id && item.pending);
    if (!hasPending) {
      return [
        ...prev.filter((item) => item.id !== STATUS_ID),
        {
          id: uid(),
          kind: "agent",
          title: String(event.name || "Agent"),
          meta: `${event.role || ""} · ${Number(event.elapsed_seconds || 0).toFixed(1)}s`,
          body,
          accent: String(event.accent || "#3aa89a"),
          agentId: String(event.agent_id || ""),
          pending: false,
          jobId,
          imageFiles,
        },
      ];
    }
    return prev.map((item) =>
      item.agentId === event.agent_id && item.pending
        ? {
            ...item,
            pending: false,
            meta: `${event.role || item.meta || ""} · ${Number(event.elapsed_seconds || 0).toFixed(1)}s`,
            body,
            jobId: jobId || item.jobId,
            imageFiles: imageFiles || item.imageFiles,
            artifacts: item.artifacts,
          }
        : item,
    );
  }
  if (type === "agent_error" || type === "fatal" || type === "job_cancelled") {
    return upsertStatus(
      prev,
      type === "job_cancelled" ? "Cancelled" : "Error",
      String(event.error || "cancelled").slice(0, 180),
      true,
    );
  }
  if (type === "session_done" || type === "agentic_done") {
    const errors = Array.isArray(event.errors) ? event.errors.join("; ") : "";
    return upsertStatus(
      prev,
      errors ? "Finished with issues" : "Done",
      `${Number(event.total_seconds || 0).toFixed(1)}s${errors ? ` · ${errors.slice(0, 100)}` : ""}`,
      Boolean(errors),
    );
  }
  return prev;
}

export function buildFeedFromEvents(
  events: DebateEvent[],
  userMessage?: string,
  images?: ChatImage[],
): FeedItem[] {
  let feed: FeedItem[] = [];
  if (userMessage || (images && images.length > 0)) {
    feed = [
      {
        id: uid(),
        kind: "user",
        title: "You",
        body: userMessage || "",
        images,
      },
    ];
  }
  for (const event of events) {
    feed = reduceFeed(feed, event);
  }
  return feed;
}

export function appendUserMessage(
  prev: FeedItem[],
  text: string,
  meta?: string,
  images?: ChatImage[],
): FeedItem[] {
  const cleaned = prev.filter((item) => item.id !== STATUS_ID);
  const last = cleaned[cleaned.length - 1];
  if (
    last?.kind === "user" &&
    last.body.trim() === text.trim() &&
    (last.images?.length || 0) === (images?.length || 0)
  ) {
    return cleaned;
  }
  return [
    ...cleaned,
    {
      id: uid(),
      kind: "user",
      title: "You",
      meta,
      body: text,
      images,
    },
  ];
}
