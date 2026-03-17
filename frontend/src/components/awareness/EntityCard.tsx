/**
 * EntityCard - Social network entity display card
 * Shows contact info, communication style, related jobs, expertise, and relationship strength
 */

import { useState } from 'react';
import {
  User, Tag, Clock, ChevronDown, ChevronRight, Mail, Phone,
  Building, Activity, Briefcase, UserCircle, Star,
} from 'lucide-react';
import { Badge, Markdown } from '@/components/ui';
import { cn, formatRelativeTime } from '@/lib/utils';
import type { SocialNetworkEntity } from '@/types';

interface EntityCardProps {
  entity: SocialNetworkEntity;
  isCurrentUser: boolean;
  actualChatCount: number;
}

export function EntityCard({ entity, isCurrentUser, actualChatCount }: EntityCardProps) {
  const [isExpanded, setIsExpanded] = useState(isCurrentUser);

  // Calculate strength level for visual indicator
  const strengthLevel = entity.relationship_strength >= 0.7 ? 'high' : entity.relationship_strength >= 0.4 ? 'medium' : 'low';

  return (
    <div
      className={cn(
        'rounded-xl border overflow-hidden transition-all duration-300',
        isCurrentUser
          ? 'border-[var(--accent-primary)]/30 bg-[var(--accent-glow)] shadow-[0_0_20px_var(--accent-glow)]'
          : isExpanded
            ? 'border-[var(--border-default)] bg-[var(--bg-elevated)] shadow-lg'
            : 'border-[var(--border-subtle)] bg-[var(--bg-sunken)] hover:border-[var(--border-default)] hover:bg-[var(--bg-elevated)]'
      )}
    >
      {/* Header - always visible */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full p-3 flex items-center gap-3 text-left transition-all duration-300 group"
      >
        <span className={cn(
          'transition-all duration-300',
          isExpanded ? 'text-[var(--accent-primary)]' : 'text-[var(--text-tertiary)]'
        )}>
          {isExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5 group-hover:translate-x-0.5 transition-transform" />}
        </span>

        <div className={cn(
          'w-9 h-9 rounded-xl flex items-center justify-center shrink-0 transition-all duration-300',
          isCurrentUser
            ? 'bg-[var(--accent-primary)] shadow-[0_0_15px_var(--accent-primary)]'
            : 'bg-[var(--bg-tertiary)] border border-[var(--border-subtle)] group-hover:border-[var(--accent-primary)]/30'
        )}>
          <User className={cn(
            'w-4 h-4 transition-colors',
            isCurrentUser ? 'text-[var(--bg-deep)]' : 'text-[var(--text-secondary)]'
          )} />
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-[var(--text-primary)] truncate group-hover:text-[var(--accent-primary)] transition-colors">
              {entity.entity_name || entity.entity_id}
            </span>
            {isCurrentUser && (
              <span className="text-[9px] px-2 py-0.5 rounded-full bg-[var(--accent-primary)] text-[var(--bg-deep)] font-medium uppercase tracking-wider">
                You
              </span>
            )}
          </div>
          <div className="text-[10px] text-[var(--text-tertiary)] font-mono truncate mt-0.5 flex items-center gap-2">
            <span>{entity.entity_type}</span>
            <span className="w-1 h-1 rounded-full bg-[var(--text-tertiary)]" />
            <span>{actualChatCount} chats</span>
          </div>
        </div>

        {/* Strength indicator */}
        <div className="flex items-center gap-2">
          {strengthLevel === 'high' && (
            <Badge variant="success" size="sm" glow>Strong</Badge>
          )}
          {strengthLevel === 'medium' && (
            <Badge variant="warning" size="sm">Medium</Badge>
          )}
        </div>
      </button>

      {/* Expanded content */}
      {isExpanded && (
        <div className="border-t border-[var(--border-subtle)] p-3 space-y-3 bg-[var(--bg-sunken)]/50 animate-fade-in">
          {/* Persona - Communication Style */}
          {entity.persona && (
            <div className="p-3 bg-[var(--accent-primary)]/5 rounded-lg border border-[var(--accent-primary)]/20">
              <div className="text-[9px] text-[var(--accent-primary)] font-medium uppercase tracking-wider flex items-center gap-1.5 mb-2">
                <UserCircle className="w-3 h-3" />
                Communication Style
              </div>
              <div className="text-xs text-[var(--text-secondary)] leading-relaxed">
                {entity.persona}
              </div>
            </div>
          )}

          {/* Related Jobs */}
          {entity.related_job_ids && entity.related_job_ids.length > 0 && (
            <div className="p-3 bg-[var(--color-warning)]/5 rounded-lg border border-[var(--color-warning)]/20">
              <div className="text-[9px] text-[var(--color-warning)] font-medium uppercase tracking-wider flex items-center gap-1.5 mb-2">
                <Briefcase className="w-3 h-3" />
                Related Jobs ({entity.related_job_ids.length})
              </div>
              <div className="flex flex-wrap gap-1.5">
                {entity.related_job_ids.map((jobId, index) => (
                  <span
                    key={index}
                    className="inline-flex items-center px-2 py-1 text-[9px] rounded-lg bg-[var(--color-warning)]/10 text-[var(--color-warning)] border border-[var(--color-warning)]/20 font-mono"
                    title={jobId}
                  >
                    {jobId.length > 12 ? `${jobId.slice(0, 12)}...` : jobId}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Expertise Domains */}
          {entity.expertise_domains && entity.expertise_domains.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {entity.expertise_domains.map((domain, index) => (
                <span
                  key={index}
                  className="inline-flex items-center gap-1 px-2 py-1 text-[9px] rounded-lg bg-[var(--color-success)]/10 text-[var(--color-success)] border border-[var(--color-success)]/20 font-mono"
                >
                  <Star className="w-2.5 h-2.5" />
                  {domain}
                </span>
              ))}
            </div>
          )}

          {/* Description */}
          {entity.entity_description && (
            <div className="text-xs text-[var(--text-secondary)] leading-relaxed p-3 bg-[var(--bg-elevated)] rounded-lg border border-[var(--border-subtle)]">
              <Markdown content={entity.entity_description} />
            </div>
          )}

          {/* Tags */}
          {entity.tags && entity.tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {entity.tags.map((tag, index) => (
                <span
                  key={index}
                  className="inline-flex items-center gap-1 px-2 py-1 text-[9px] rounded-lg bg-[var(--accent-secondary)]/10 text-[var(--accent-secondary)] border border-[var(--accent-secondary)]/20 font-mono"
                >
                  <Tag className="w-2.5 h-2.5" />
                  {tag}
                </span>
              ))}
            </div>
          )}

          {/* Identity Info */}
          {entity.identity_info && Object.keys(entity.identity_info).length > 0 && (
            <div className="space-y-2 p-3 bg-[var(--bg-elevated)] rounded-lg border border-[var(--border-subtle)]">
              <div className="text-[9px] text-[var(--accent-primary)] font-medium uppercase tracking-wider flex items-center gap-1.5">
                <Building className="w-3 h-3" />
                Identity
              </div>
              <div className="grid grid-cols-1 gap-1 text-[10px] font-mono">
                {Object.entries(entity.identity_info).map(([key, value]) => (
                  <div key={key} className="flex items-start gap-2">
                    <span className="text-[var(--text-tertiary)] capitalize min-w-[60px]">{key}:</span>
                    <span className="text-[var(--text-secondary)] break-all">
                      {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Contact Info */}
          {entity.contact_info && Object.keys(entity.contact_info).length > 0 && (
            <div className="space-y-2 p-3 bg-[var(--bg-elevated)] rounded-lg border border-[var(--border-subtle)]">
              <div className="text-[9px] text-[var(--color-success)] font-medium uppercase tracking-wider flex items-center gap-1.5">
                <Mail className="w-3 h-3" />
                Contact
              </div>
              <div className="grid grid-cols-1 gap-1 text-[10px] font-mono">
                {Object.entries(entity.contact_info).map(([key, value]) => (
                  <div key={key} className="flex items-center gap-2">
                    {key === 'email' && <Mail className="w-3 h-3 text-[var(--text-tertiary)]" />}
                    {key === 'phone' && <Phone className="w-3 h-3 text-[var(--text-tertiary)]" />}
                    {!['email', 'phone'].includes(key) && <span className="text-[var(--text-tertiary)] capitalize min-w-[60px]">{key}:</span>}
                    <span className="text-[var(--text-secondary)] truncate">
                      {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Stats */}
          <div className="flex items-center justify-between pt-2 border-t border-[var(--border-subtle)] text-[9px] font-mono">
            <div className="flex items-center gap-2 text-[var(--text-tertiary)]">
              <Activity className="w-3 h-3 text-[var(--accent-primary)]" />
              <span>Strength:</span>
              <span className={cn(
                'font-medium',
                strengthLevel === 'high' && 'text-[var(--color-success)]',
                strengthLevel === 'medium' && 'text-[var(--color-warning)]',
                strengthLevel === 'low' && 'text-[var(--text-tertiary)]'
              )}>
                {(entity.relationship_strength * 100).toFixed(0)}%
              </span>
            </div>
            {entity.last_interaction_time && (
              <div className="flex items-center gap-1.5 text-[var(--text-tertiary)]">
                <Clock className="w-3 h-3" />
                {formatRelativeTime(entity.last_interaction_time)}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
