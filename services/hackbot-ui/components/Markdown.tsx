"use client";

import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { formatFence, stripJsonFences } from "@/lib/findings-format";

// Shared markdown renderer for finding text and proposed action previews.
// Safe by construction: react-markdown does not render raw HTML and does not
// use dangerouslySetInnerHTML, so untrusted content can't inject markup.
const components: Components = {
  // External links open in a new tab.
  a({ children, href }) {
    return (
      <a href={href} target="_blank" rel="noreferrer">
        {children}
      </a>
    );
  },
  // Fenced code blocks carry a `language-*` class (or span multiple lines);
  // inline code has neither. Pretty-print JSON fences to preserve the prior
  // friendly-findings behavior.
  code({ className, children, ...props }) {
    const raw = String(children ?? "");
    const match = /language-(\w+)/.exec(className ?? "");
    if (match || raw.includes("\n")) {
      return (
        <code className={className} {...props}>
          {formatFence(match?.[1] ?? "", raw)}
        </code>
      );
    }
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  },
};

export function Markdown({
  text,
  dropJsonBlocks = false,
}: {
  text: string;
  dropJsonBlocks?: boolean;
}) {
  const content = dropJsonBlocks ? stripJsonFences(text) : text;
  return (
    <div className="finding-text markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
