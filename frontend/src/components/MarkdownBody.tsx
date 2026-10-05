"use client";

import ReactMarkdown from "react-markdown";

type Props = {
  content: string;
  pending?: boolean;
};

/** Normalize model quirks so markdown renders cleanly. */
function normalizeMarkdown(raw: string): string {
  let text = raw.replace(/\r\n/g, "\n").trim();

  // Bullet glyphs some local models emit instead of "-"
  text = text.replace(/^[•●▪◦]\s+/gm, "- ");
  // "� **" leftovers / odd replacement chars before bold bullets
  text = text.replace(/^[\uFFFD?]+\s*/gm, "");
  // Ensure a blank line before headings so parsers treat them as headings
  text = text.replace(/([^\n])\n(#{1,4}\s)/g, "$1\n\n$2");
  // Ensure list items are on their own lines after a colon-ended intro
  text = text.replace(/:\s*\n([-*])/g, ":\n\n$1");

  return text;
}

export default function MarkdownBody({ content, pending }: Props) {
  if (pending) {
    return <div className="bubble-body pending-text">{content}</div>;
  }

  return (
    <div className="md-body">
      <ReactMarkdown
        components={{
          h1: ({ children }) => <h3 className="md-h">{children}</h3>,
          h2: ({ children }) => <h3 className="md-h">{children}</h3>,
          h3: ({ children }) => <h4 className="md-h">{children}</h4>,
          h4: ({ children }) => <h4 className="md-h">{children}</h4>,
          p: ({ children }) => <p className="md-p">{children}</p>,
          ul: ({ children }) => <ul className="md-ul">{children}</ul>,
          ol: ({ children }) => <ol className="md-ol">{children}</ol>,
          li: ({ children }) => <li className="md-li">{children}</li>,
          strong: ({ children }) => <strong className="md-strong">{children}</strong>,
          em: ({ children }) => <em>{children}</em>,
          code: ({ children }) => <code className="md-code">{children}</code>,
          pre: ({ children }) => <pre className="md-pre">{children}</pre>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer" className="md-a">
              {children}
            </a>
          ),
        }}
      >
        {normalizeMarkdown(content)}
      </ReactMarkdown>
    </div>
  );
}
