import type { CSSProperties } from "react";

type Props = {
  kind: "user" | "agent" | "system";
  title: string;
  accent?: string;
};

function initials(title: string, kind: Props["kind"]) {
  if (kind === "user") return "U";
  if (kind === "system") return "i";
  const parts = title.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "A";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
}

export default function MessageAvatar({ kind, title, accent }: Props) {
  return (
    <div
      className={`msg-avatar ${kind}`}
      style={accent ? ({ ["--accent"]: accent } as CSSProperties) : undefined}
      aria-hidden
      title={title}
    >
      {initials(title, kind)}
    </div>
  );
}
