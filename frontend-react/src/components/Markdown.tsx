import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Renders assistant answers as markdown with Aria's spacing/typography.
// Component overrides keep the look on-brand (no default browser margins).
export function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
        ul: ({ children }) => <ul className="mb-3 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>,
        ol: ({ children }) => <ol className="mb-3 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>,
        li: ({ children }) => <li className="leading-[1.5]">{children}</li>,
        strong: ({ children }) => <strong className="font-semibold text-text-ink">{children}</strong>,
        h1: ({ children }) => <h1 className="mb-2 mt-1 text-[18px] font-semibold">{children}</h1>,
        h2: ({ children }) => <h2 className="mb-2 mt-1 text-[16px] font-semibold">{children}</h2>,
        h3: ({ children }) => <h3 className="mb-1 mt-1 text-[15px] font-semibold">{children}</h3>,
        code: ({ children }) => (
          <code className="rounded bg-canvas px-1 py-0.5 font-mono text-[13px]">{children}</code>
        ),
        a: ({ children, href }) => (
          <a href={href} className="text-marigold underline" target="_blank" rel="noreferrer">
            {children}
          </a>
        ),
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
