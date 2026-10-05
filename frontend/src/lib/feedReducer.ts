import type { DebateEvent, FeedItem, ToolStep } from "@/lib/types";
import { formatAgentOutput, formatToolResult, summarizeArgs } from "@/lib/format";

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
    const intent = String(event.intent || resolved);
    const reason = String(event.reason || "").slice(0, 80);
    const label =
      requested === "auto" || requested === resolved
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
  if (type === "agent_done") {
    const rawOut = event.output;
    const asText =
      typeof rawOut === "string"
        ? rawOut
        : rawOut == null
          ? ""
          : JSON.stringify(rawOut, null, 2);
    const body = formatAgentOutput(asText);
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
): FeedItem[] {
  let feed: FeedItem[] = [];
  if (userMessage) {
    feed = [
      {
        id: uid(),
        kind: "user",
        title: "You",
        body: userMessage,
      },
    ];
  }
  for (const event of events) {
    feed = reduceFeed(feed, event);
  }
  return feed;
}

export function appendUserMessage(prev: FeedItem[], text: string, meta?: string): FeedItem[] {
  const cleaned = prev.filter((item) => item.id !== STATUS_ID);
  const last = cleaned[cleaned.length - 1];
  if (last?.kind === "user" && last.body.trim() === text.trim()) {
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
    },
  ];
}
