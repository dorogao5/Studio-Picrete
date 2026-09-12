import "katex/dist/katex.min.css";
import { memo, type ComponentPropsWithoutRef } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { MathViewport } from "./MathViewport";
import remarkReadableMath, { normalizeEscapedLineBreaks } from "../lib/remarkReadableMath";

import { clsx as cn } from "clsx";

interface RichTextProps {
  children: string;
  className?: string;
  inline?: boolean;
}

const ExternalImage = ({ src, alt, ...props }: ComponentPropsWithoutRef<"img">) => {
  if (!src) return null;

  const imageAlt = alt?.trim() || "Иллюстрация к материалу";
  const className = cn("my-4 max-h-[32rem] max-w-full rounded-md border object-contain", props.className);

  return <img {...props} src={src} alt={imageAlt} loading="lazy" decoding="async" className={className} />;
};

const createComponents = (inline: boolean): Components => ({
  h1: ({ children }) =>
    inline ? <strong>{children}</strong> : <h2 className="mb-3 mt-5 text-xl font-semibold first:mt-0">{children}</h2>,
  h2: ({ children }) =>
    inline ? <strong>{children}</strong> : <h3 className="mb-2 mt-5 text-lg font-semibold first:mt-0">{children}</h3>,
  h3: ({ children }) =>
    inline ? <strong>{children}</strong> : <h4 className="mb-2 mt-4 font-semibold first:mt-0">{children}</h4>,
  p: ({ children }) =>
    inline ? <span>{children}</span> : <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
  li: ({ children }) => inline ? <span role="listitem" className="math-inline-item block">{children}</span> : <li>{children}</li>,
  ul: ({ children }) => inline ? <span role="list" className="math-inline-bullets block">{children}</span> : <ul className="my-3 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children, start }) => inline ? <span role="list" className="math-inline-numbers block" style={{ counterReset: `math-item ${(start ?? 1) - 1}` }}>{children}</span> : <ol start={start} className="my-3 list-decimal space-y-1 pl-5">{children}</ol>,
  blockquote: ({ children }) => (
    <blockquote className="my-3 border-l-2 border-accent/50 pl-3 text-muted-foreground">{children}</blockquote>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="font-medium text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
    >
      {children}
      <span className="sr-only"> (откроется в новой вкладке)</span>
    </a>
  ),
  table: ({ children }) => (
    <div className="my-4 max-w-full overflow-x-auto rounded-md border">
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => <th className="border-b bg-muted/70 px-3 py-2 font-semibold">{children}</th>,
  td: ({ children }) => <td className="border-b px-3 py-2 align-top last:border-r-0">{children}</td>,
  pre: ({ children }) => (
    <pre className="my-3 max-w-full overflow-x-auto rounded-md bg-foreground p-3 text-sm text-background">
      {children}
    </pre>
  ),
  code: ({ children, className }) => (
    <code className={cn("rounded bg-muted px-1 py-0.5 font-mono text-[0.92em]", className)}>{children}</code>
  ),
  span: ({ node, children, ...props }) => props.className?.split(" ").includes("katex")
    ? <MathViewport><span {...props}>{children}</span></MathViewport>
    : <span {...props}>{children}</span>,
  img: ExternalImage,
});

const blockComponents = createComponents(false);
const inlineComponents = createComponents(true);

/** Safe Markdown/GFM renderer using the same readable-math pipeline as Picrete. */
function MathText({ children, className, inline = false }: RichTextProps) {
  const Wrapper = inline ? "span" : "div";
  return (
    <Wrapper className={cn("math-text academic-content min-w-0 max-w-full leading-relaxed", inline && "inline", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath, remarkReadableMath, remarkBreaks]}
        rehypePlugins={[rehypeRaw, rehypeSanitize, [rehypeKatex, { strict: "ignore" }]]}
        components={inline ? inlineComponents : blockComponents}
      >
        {normalizeEscapedLineBreaks(children)
          .replace(/\\\(([\s\S]*?)\\\)/g, (_, math: string) => `$${math}$`)
          .replace(/\\\[([\s\S]*?)\\\]/g, (_, math: string) => `\n\n$$\n${math}\n$$\n\n`)}
      </ReactMarkdown>
    </Wrapper>
  );
}

export default memo(MathText);
