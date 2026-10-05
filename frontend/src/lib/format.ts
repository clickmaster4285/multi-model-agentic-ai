/** Turn raw tool / model output into readable chat text. */
export function formatToolResult(raw: string): string {
  const text = String(raw || "").trim();
  if (!text) return "No output.";
  if (text.startsWith("TOOL_ERROR:")) {
    return text.replace(/^TOOL_ERROR:\s*/, "Error: ");
  }

  try {
    const data = JSON.parse(text);
    if (Array.isArray(data)) {
      return data.map((item, index) => `${index + 1}. ${String(item)}`).join("\n");
    }
    if (data && typeof data === "object") {
      return Object.entries(data as Record<string, unknown>)
        .map(([key, value]) => `- **${key}**: ${typeof value === "string" ? value : JSON.stringify(value)}`)
        .join("\n");
    }
  } catch {
    // not JSON
  }

  // Already a long blob — keep chat scannable
  if (text.length > 900) return `${text.slice(0, 900)}…`;
  return text;
}

export function summarizeArgs(args: unknown): string {
  if (!args || typeof args !== "object") return "";
  const entries = Object.entries(args as Record<string, unknown>);
  if (entries.length === 0) return "";
  return entries
    .map(([key, value]) => {
      const text = typeof value === "string" ? value : JSON.stringify(value);
      return `${key}: ${text.length > 60 ? `${text.slice(0, 60)}…` : text}`;
    })
    .join(" · ");
}
