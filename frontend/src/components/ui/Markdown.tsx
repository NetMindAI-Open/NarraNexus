/**
 * Markdown renderer component
 * Renders markdown content with syntax highlighting and GFM support
 */

import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { cn } from '@/lib/utils';

interface MarkdownProps {
  content: string;
  className?: string;
  compact?: boolean;
}

// memo matters here: remark/rehype re-parse the whole string on every
// render, and chat surfaces re-render on every WebSocket delta. All props
// are primitives, so shallow equality skips the re-parse for content that
// didn't change (e.g. earlier segments while the last one streams).
export const Markdown = memo(function Markdown({ content, className, compact = false }: MarkdownProps) {
  return (
    <div className={cn(
      'markdown-content',
      compact && 'markdown-compact',
      className
    )}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw]}
        components={{
          // Custom link handling - open external links in new tab
          a: ({ href, children, ...props }) => {
            const isExternal = href?.startsWith('http');
            return (
              <a
                href={href}
                target={isExternal ? '_blank' : undefined}
                rel={isExternal ? 'noopener noreferrer' : undefined}
                {...props}
              >
                {children}
              </a>
            );
          },
          // Add copy button to code blocks
          pre: ({ children, ...props }) => (
            <pre className="group relative" {...props}>
              {children}
            </pre>
          ),
          // Style inline code
          code: ({ className: codeClassName, children, ...props }) => {
            const isInline = !codeClassName;
            return (
              <code
                className={cn(
                  isInline && 'inline-code',
                  codeClassName
                )}
                {...props}
              >
                {children}
              </code>
            );
          },
          // Style tables
          table: ({ children, ...props }) => (
            <div className="overflow-x-auto">
              <table {...props}>{children}</table>
            </div>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

// Compact version for message previews
export function MarkdownPreview({ content, maxLength = 200 }: { content: string; maxLength?: number }) {
  const truncated = content.length > maxLength
    ? content.slice(0, maxLength) + '...'
    : content;

  return (
    <Markdown
      content={truncated}
      compact
      className="text-[var(--text-secondary)] line-clamp-2"
    />
  );
}
