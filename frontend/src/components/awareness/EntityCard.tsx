/**
 * EntityCard — a single contact row in the social-network list.
 *
 * Nordic-archive style: flat row separated from siblings by a hairline
 * bottom rule, no border-box. Expanded detail is a series of label/value
 * pairs divided by thin rules — no nested colored tinted cards.
 */

import { useState } from 'react';
import {
  User, Tag, Clock, ChevronDown, ChevronRight, Mail, Phone,
  Building, Briefcase, Star, Link,
} from 'lucide-react';
import { Markdown } from '@/components/ui';
import { cn, formatRelativeTime } from '@/lib/utils';
import type { SocialNetworkEntity } from '@/types';

interface EntityCardProps {
  entity: SocialNetworkEntity;
  isCurrentUser: boolean;
  actualChatCount: number;
}

type StrengthLevel = 'high' | 'medium' | 'low';

const strengthLabel: Record<StrengthLevel, string> = {
  high: 'Strong',
  medium: 'Medium',
  low: 'Weak',
};

const strengthText: Record<StrengthLevel, string> = {
  high: 'text-[var(--color-green-500)]',
  medium: 'text-[var(--color-yellow-500)]',
  low: 'text-[var(--text-tertiary)]',
};

export function EntityCard({ entity, isCurrentUser, actualChatCount }: EntityCardProps) {
  const [isExpanded, setIsExpanded] = useState(isCurrentUser);

  const strengthLevel: StrengthLevel =
    entity.relationship_strength >= 0.7 ? 'high'
    : entity.relationship_strength >= 0.4 ? 'medium'
    : 'low';

  return (
    <div
      className={cn(
        'border-b border-[var(--rule)] last:border-b-0 transition-colors duration-150',
        isCurrentUser && 'bg-[var(--bg-secondary)]'
      )}
    >
      {/* Collapsed header — always visible */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full py-2.5 flex items-center gap-3 text-left group"
      >
        <span className="w-3.5 text-[var(--text-tertiary)] group-hover:text-[var(--text-primary)] transition-colors">
          {isExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </span>

        <div
          className={cn(
            'w-8 h-8 flex items-center justify-center shrink-0',
            isCurrentUser
              ? 'bg-[var(--text-primary)] text-[var(--text-inverse)]'
              : 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)]'
          )}
        >
          <User className="w-3.5 h-3.5" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm text-[var(--text-primary)] truncate">
              {entity.entity_name || entity.entity_id}
            </span>
            {isCurrentUser && (
              <span className="text-[9px] px-1.5 py-[1px] bg-[var(--text-primary)] text-[var(--text-inverse)] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em]">
                You
              </span>
            )}
          </div>
          <div className="mt-0.5 text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.1em] flex items-center gap-2">
            <span className="truncate">{entity.entity_type}</span>
            <span className="opacity-40">·</span>
            <span>{actualChatCount} chats</span>
            {entity.familiarity && (
              <>
                <span className="opacity-40">·</span>
                <span>{entity.familiarity === 'direct' ? 'Direct' : 'Known of'}</span>
              </>
            )}
          </div>
        </div>

        {/* Strength label */}
        <span
          className={cn(
            'text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em]',
            strengthText[strengthLevel]
          )}
        >
          {strengthLabel[strengthLevel]}
        </span>
      </button>

      {/* Expanded content — flat label/value strip */}
      {isExpanded && (
        <div className="pb-3 pl-[calc(0.875rem+2rem+0.75rem)] pr-1 space-y-3 animate-fade-in">
          <Detail
            icon={User}
            label="Communication Style"
            show={!!entity.persona}
          >
            <p className="leading-relaxed">{entity.persona}</p>
          </Detail>

          <Detail
            icon={Briefcase}
            label={`Related Jobs (${entity.related_job_ids?.length ?? 0})`}
            show={!!(entity.related_job_ids && entity.related_job_ids.length > 0)}
          >
            <div className="flex flex-wrap gap-1.5 font-[family-name:var(--font-mono)] text-[11px]">
              {entity.related_job_ids?.map((jobId, i) => (
                <span
                  key={i}
                  title={jobId}
                  className="border border-[var(--border-subtle)] px-1.5 py-[1px] text-[var(--text-secondary)]"
                >
                  {jobId.length > 12 ? `${jobId.slice(0, 12)}…` : jobId}
                </span>
              ))}
            </div>
          </Detail>

          <Detail
            icon={Star}
            label="Expertise"
            show={!!(entity.expertise_domains && entity.expertise_domains.length > 0)}
          >
            <div className="flex flex-wrap gap-1.5 text-[11px]">
              {entity.expertise_domains?.map((d, i) => (
                <span
                  key={i}
                  className="border border-[var(--border-subtle)] px-1.5 py-[1px] text-[var(--text-secondary)]"
                >
                  {d}
                </span>
              ))}
            </div>
          </Detail>

          {entity.entity_description && (
            <Detail label="Description" show>
              <div className="text-[13px] text-[var(--text-secondary)] leading-relaxed">
                <Markdown content={entity.entity_description} />
              </div>
            </Detail>
          )}

          <Detail
            icon={Link}
            label="Aliases"
            show={!!(entity.aliases && entity.aliases.length > 0)}
          >
            <div className="flex flex-wrap gap-1.5 text-[11px] font-[family-name:var(--font-mono)]">
              {entity.aliases?.map((alias, i) => (
                <span key={i} className="text-[var(--text-tertiary)]">
                  {alias}{i < (entity.aliases?.length ?? 0) - 1 && <span className="opacity-40"> · </span>}
                </span>
              ))}
            </div>
          </Detail>

          <Detail
            icon={Tag}
            label="Tags"
            show={!!(entity.tags && entity.tags.length > 0)}
          >
            <div className="flex flex-wrap gap-1.5 text-[11px] font-[family-name:var(--font-mono)]">
              {entity.tags?.map((t, i) => (
                <span key={i} className="text-[var(--text-tertiary)]">
                  #{t}
                </span>
              ))}
            </div>
          </Detail>

          <KvGroup
            icon={Building}
            label="Identity"
            entries={entity.identity_info}
          />

          <KvGroup
            icon={Mail}
            label="Contact"
            entries={entity.contact_info}
            iconOverride={(key) =>
              key === 'email' ? <Mail className="w-3 h-3 text-[var(--text-tertiary)]" /> :
              key === 'phone' ? <Phone className="w-3 h-3 text-[var(--text-tertiary)]" /> :
              null
            }
          />

          <div className="flex items-center justify-between pt-2 border-t border-[var(--rule)] text-[10px] font-[family-name:var(--font-mono)] uppercase tracking-[0.12em] text-[var(--text-tertiary)]">
            <span className="flex items-center gap-1.5">
              Strength
              <span className={cn('tabular-nums', strengthText[strengthLevel])}>
                {(entity.relationship_strength * 100).toFixed(0)}%
              </span>
            </span>
            {entity.last_interaction_time && (
              <span className="flex items-center gap-1.5">
                <Clock className="w-3 h-3" />
                {formatRelativeTime(entity.last_interaction_time)}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────── helpers ──────────── */

interface DetailProps {
  icon?: React.ElementType;
  label: string;
  show?: boolean;
  children: React.ReactNode;
}

function Detail({ icon: Icon, label, show = true, children }: DetailProps) {
  if (!show) return null;
  return (
    <div>
      <div className="text-[10px] text-[var(--text-tertiary)] font-[family-name:var(--font-mono)] uppercase tracking-[0.14em] flex items-center gap-1.5 mb-1.5">
        {Icon && <Icon className="w-3 h-3" />}
        {label}
      </div>
      <div className="text-[13px] text-[var(--text-secondary)]">{children}</div>
    </div>
  );
}

interface KvGroupProps {
  icon: React.ElementType;
  label: string;
  entries?: Record<string, unknown>;
  iconOverride?: (key: string) => React.ReactNode;
}

function KvGroup({ icon: Icon, label, entries, iconOverride }: KvGroupProps) {
  if (!entries || Object.keys(entries).length === 0) return null;
  return (
    <Detail icon={Icon} label={label}>
      <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-[11px] font-[family-name:var(--font-mono)]">
        {Object.entries(entries).map(([k, v]) => (
          <Fragment key={k}>
            <dt className="text-[var(--text-tertiary)] uppercase tracking-[0.08em] flex items-center gap-1">
              {iconOverride?.(k) ?? null}
              {k}
            </dt>
            <dd className="text-[var(--text-primary)] break-all">
              {typeof v === 'object' ? JSON.stringify(v) : String(v)}
            </dd>
          </Fragment>
        ))}
      </dl>
    </Detail>
  );
}

// Fragment wrapper so we don't need to import it
import { Fragment } from 'react';
