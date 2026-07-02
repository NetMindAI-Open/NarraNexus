/**
 * MCP Manager Component - Manage MCP SSE URLs for agent
 *
 * Features:
 * - List all MCPs for agent+user
 * - Add new MCP with name and URL
 * - Delete MCP
 * - Toggle enable/disable
 * - Validate connection status (green/red indicator)
 * - Refresh and validate all MCPs
 */

import { useState, useCallback, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Server,
  Plus,
  Trash2,
  RefreshCw,
  CheckCircle,
  XCircle,
  Circle,
  Power,
  AlertCircle,
} from 'lucide-react';
import { Button, Badge, ScrollArea, useConfirm } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import type { MCPInfo } from '@/types';

interface MCPItemProps {
  mcp: MCPInfo;
  onDelete: (mcpId: string) => void;
  onToggle: (mcpId: string, enabled: boolean) => void;
  onValidate: (mcpId: string) => void;
  validating: boolean;
}

function MCPItem({ mcp, onDelete, onToggle, onValidate, validating }: MCPItemProps) {
  const { t } = useTranslation();
  const getStatusIcon = () => {
    if (validating) {
      return <RefreshCw className="w-3 h-3 animate-spin text-[var(--text-tertiary)]" />;
    }

    switch (mcp.connection_status) {
      case 'connected':
        return <CheckCircle className="w-3 h-3 text-[var(--color-green-500)]" />;
      case 'failed':
        return <XCircle className="w-3 h-3 text-[var(--color-red-500)]" />;
      default:
        return <Circle className="w-3 h-3 text-[var(--text-tertiary)]" />;
    }
  };

  const getStatusText = () => {
    if (validating) return t('skills.mcp.validating');
    switch (mcp.connection_status) {
      case 'connected':
        return t('skills.mcp.connected');
      case 'failed':
        return mcp.last_error ? t('skills.mcp.failedWithError', { error: mcp.last_error.slice(0, 80) }) : t('skills.mcp.failed');
      default:
        return t('skills.mcp.unknown');
    }
  };

  return (
    <div
      className={cn(
        'flex items-center gap-2 p-2 bg-[var(--bg-secondary)] rounded group hover:bg-[var(--bg-tertiary)] transition-colors',
        !mcp.is_enabled && 'opacity-50'
      )}
    >
      {/* Status Indicator */}
      <button
        onClick={() => onValidate(mcp.mcp_id)}
        className="shrink-0 p-0.5 hover:bg-[var(--bg-tertiary)] rounded"
        // Full error in the tooltip (the inline label is truncated). Click to
        // re-validate. A Failed dot here means the URL is unreachable or not a
        // valid SSE endpoint — see the add-form hints.
        title={mcp.connection_status === 'failed' && mcp.last_error ? t('skills.mcp.failedTooltip', { error: mcp.last_error }) : t('skills.mcp.statusTooltip', { status: getStatusText() })}
        disabled={validating}
      >
        {getStatusIcon()}
      </button>

      {/* MCP Info */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-xs text-[var(--text-primary)] font-medium truncate">
            {mcp.name}
          </span>
          {!mcp.is_enabled && (
            <Badge variant="default" size="sm">{t('skills.mcp.disabled')}</Badge>
          )}
        </div>
        <div className="text-[9px] text-[var(--text-tertiary)] truncate font-mono" title={mcp.url}>
          {mcp.url}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
        <Button
          variant="ghost"
          size="icon"
          onClick={() => onToggle(mcp.mcp_id, !mcp.is_enabled)}
          className="w-6 h-6"
          title={mcp.is_enabled ? t('skills.mcp.disable') : t('skills.mcp.enable')}
        >
          <Power className={cn('w-3 h-3', mcp.is_enabled ? 'text-[var(--color-green-500)]' : 'text-[var(--text-tertiary)]')} />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => onDelete(mcp.mcp_id)}
          className="w-6 h-6 text-[var(--text-tertiary)] hover:text-[var(--color-error)]"
          title={t('skills.mcp.delete')}
        >
          <Trash2 className="w-3 h-3" />
        </Button>
      </div>
    </div>
  );
}

interface AddMCPFormProps {
  onAdd: (name: string, url: string) => void;
  onCancel: () => void;
  loading: boolean;
}

function AddMCPForm({ onAdd, onCancel, loading }: AddMCPFormProps) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [url, setUrl] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (name.trim() && url.trim()) {
      onAdd(name.trim(), url.trim());
    }
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-2 p-2 bg-[var(--bg-secondary)] rounded-lg">
      <input
        type="text"
        placeholder={t('skills.mcp.namePlaceholder')}
        value={name}
        onChange={(e) => setName(e.target.value)}
        className="w-full px-2 py-1.5 text-xs bg-[var(--bg-primary)] border border-[var(--border-default)] rounded focus:outline-none focus:border-[var(--accent-primary)]"
        autoFocus
      />
      <input
        type="url"
        placeholder={t('skills.mcp.urlPlaceholder')}
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        className="w-full px-2 py-1.5 text-xs bg-[var(--bg-primary)] border border-[var(--border-default)] rounded focus:outline-none focus:border-[var(--accent-primary)] font-mono"
      />
      {/* Usability hints — the common "added it but the agent can't use it"
          confusion is almost always a non-SSE / unreachable / auth'd URL, not
          a platform bug (verified e2e 2026-05-22). Spell out the contract. */}
      <ul className="text-[10px] leading-relaxed text-[var(--text-tertiary)] list-disc pl-4 space-y-0.5">
        <li>{t('skills.mcp.hint.endpoint')}</li>
        <li>{t('skills.mcp.hint.reachable')}</li>
        <li>{t('skills.mcp.hint.noStdio')}</li>
        <li>{t('skills.mcp.hint.apiKey')}</li>
        <li>{t('skills.mcp.hint.validation')}</li>
      </ul>
      <div className="flex items-center gap-2 pt-1">
        <Button
          type="submit"
          variant="accent"
          size="sm"
          disabled={!name.trim() || !url.trim() || loading}
          className="flex-1"
        >
          {loading ? (
            <RefreshCw className="w-3 h-3 animate-spin" />
          ) : (
            t('skills.mcp.add')
          )}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onCancel}
          disabled={loading}
        >
          {t('skills.mcp.cancel')}
        </Button>
      </div>
    </form>
  );
}

export function MCPManager() {
  const { t } = useTranslation();
  const { agentId, userId } = useConfigStore();
  const [mcps, setMcps] = useState<MCPInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [validatingAll, setValidatingAll] = useState(false);
  const [validatingIds, setValidatingIds] = useState<Set<string>>(new Set());
  const [showAddForm, setShowAddForm] = useState(false);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { confirm, dialog: confirmDialog } = useConfirm();

  // Fetch MCPs on mount
  const fetchMCPs = useCallback(async () => {
    if (!agentId || !userId) return;

    setLoading(true);
    setError(null);
    try {
      const res = await api.listMCPs(agentId);
      if (res.success) {
        setMcps(res.mcps);
      } else {
        setError(res.error || t('skills.mcp.errorLoad'));
      }
    } catch (err) {
      setError(t('skills.mcp.errorLoad'));
      console.error('Error fetching MCPs:', err);
    } finally {
      setLoading(false);
    }
  }, [agentId, userId, t]);

  // Validate all MCPs on initial load
  const validateAll = useCallback(async () => {
    if (!agentId || !userId || mcps.length === 0) return;

    setValidatingAll(true);
    setValidatingIds(new Set(mcps.map(m => m.mcp_id)));

    try {
      const res = await api.validateAllMCPs(agentId);
      if (res.success) {
        // Update MCP statuses based on validation results
        setMcps(prev => prev.map(mcp => {
          const result = res.results.find(r => r.mcp_id === mcp.mcp_id);
          if (result) {
            return {
              ...mcp,
              connection_status: result.connected ? 'connected' : 'failed',
              last_error: result.error || undefined,
            };
          }
          return mcp;
        }));
      }
    } catch (err) {
      console.error('Error validating MCPs:', err);
    } finally {
      setValidatingAll(false);
      setValidatingIds(new Set());
    }
  }, [agentId, userId, mcps]);

  // Initial fetch
  useEffect(() => {
    fetchMCPs();
  }, [fetchMCPs]);

  // Validate all when MCPs are loaded
  useEffect(() => {
    if (mcps.length > 0 && !loading && !validatingAll) {
      // Only validate if we haven't validated yet (all statuses are unknown/null)
      const needsValidation = mcps.some(m => !m.connection_status || m.connection_status === 'unknown');
      if (needsValidation) {
        validateAll();
      }
    }
  }, [mcps.length, loading]);

  // Add MCP
  const handleAdd = async (name: string, url: string) => {
    if (!agentId || !userId) return;

    setAdding(true);
    setError(null);
    try {
      const res = await api.createMCP(agentId, { name, url });
      if (res.success && res.mcp) {
        setMcps(prev => [res.mcp!, ...prev]);
        setShowAddForm(false);

        // Validate the new MCP
        handleValidate(res.mcp.mcp_id);
      } else {
        setError(res.error || t('skills.mcp.errorAdd'));
      }
    } catch (err) {
      setError(t('skills.mcp.errorAdd'));
      console.error('Error adding MCP:', err);
    } finally {
      setAdding(false);
    }
  };

  // Delete MCP
  const handleDelete = async (mcpId: string) => {
    if (!agentId || !userId) return;
    const ok = await confirm({
      title: t('skills.mcp.deleteTitle'),
      message: t('skills.mcp.deleteMessage'),
      confirmText: t('skills.mcp.delete'),
      danger: true,
    });
    if (!ok) return;

    try {
      const res = await api.deleteMCP(agentId, mcpId);
      if (res.success) {
        setMcps(prev => prev.filter(m => m.mcp_id !== mcpId));
      } else {
        setError(res.error || t('skills.mcp.errorDelete'));
      }
    } catch (err) {
      setError(t('skills.mcp.errorDelete'));
      console.error('Error deleting MCP:', err);
    }
  };

  // Toggle enable/disable
  const handleToggle = async (mcpId: string, enabled: boolean) => {
    if (!agentId || !userId) return;

    try {
      const res = await api.updateMCP(agentId, mcpId, { is_enabled: enabled });
      if (res.success && res.mcp) {
        setMcps(prev => prev.map(m =>
          m.mcp_id === mcpId ? { ...m, is_enabled: enabled } : m
        ));
      } else {
        setError(res.error || t('skills.mcp.errorUpdate'));
      }
    } catch (err) {
      setError(t('skills.mcp.errorUpdate'));
      console.error('Error updating MCP:', err);
    }
  };

  // Validate single MCP
  const handleValidate = async (mcpId: string) => {
    if (!agentId || !userId) return;

    setValidatingIds(prev => new Set(prev).add(mcpId));

    try {
      const res = await api.validateMCP(agentId, mcpId);
      if (res.success) {
        setMcps(prev => prev.map(m =>
          m.mcp_id === mcpId ? {
            ...m,
            connection_status: res.connected ? 'connected' : 'failed',
            last_error: res.error || undefined,
          } : m
        ));
      }
    } catch (err) {
      console.error('Error validating MCP:', err);
    } finally {
      setValidatingIds(prev => {
        const next = new Set(prev);
        next.delete(mcpId);
        return next;
      });
    }
  };

  // Refresh and validate all
  const handleRefresh = async () => {
    await fetchMCPs();
    // validateAll will be triggered by the useEffect
  };

  // Get enabled MCPs that are connected
  const connectedEnabledCount = mcps.filter(
    m => m.is_enabled && m.connection_status === 'connected'
  ).length;

  return (
    <section className="space-y-2">
      {confirmDialog}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)] font-medium uppercase tracking-wider">
          <Server className="w-3 h-3" />
          {t('skills.mcp.servers')}
        </div>
        <div className="flex items-center gap-1">
          <Badge variant="default" size="sm">
            {connectedEnabledCount}/{mcps.length}
          </Badge>
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setShowAddForm(true)}
            className="w-6 h-6"
            title={t('skills.mcp.addMcp')}
          >
            <Plus className="w-3 h-3" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={handleRefresh}
            disabled={loading || validatingAll}
            className="w-6 h-6"
            title={t('skills.mcp.refreshValidate')}
          >
            <RefreshCw className={cn('w-3 h-3', (loading || validatingAll) && 'animate-spin')} />
          </Button>
        </div>
      </div>

      {/* Add Form */}
      {showAddForm && (
        <AddMCPForm
          onAdd={handleAdd}
          onCancel={() => setShowAddForm(false)}
          loading={adding}
        />
      )}

      {/* Error Message */}
      {error && (
        <div className="flex items-center gap-1.5 text-xs text-[var(--color-error)] p-2 border border-[var(--color-red-500)]">
          <AlertCircle className="w-3 h-3 shrink-0" />
          {error}
        </div>
      )}

      {/* MCP List */}
      {loading ? (
        <div className="space-y-1">
          <div className="animate-pulse bg-[var(--bg-secondary)] rounded h-12" />
          <div className="animate-pulse bg-[var(--bg-secondary)] rounded h-12" />
        </div>
      ) : mcps.length === 0 ? (
        <div className="text-xs text-[var(--text-tertiary)] text-center py-3 bg-[var(--bg-secondary)] rounded-lg">
          <Server className="w-5 h-5 mx-auto mb-1 opacity-50" />
          {t('skills.mcp.noServers')}
          <button
            onClick={() => setShowAddForm(true)}
            className="block mx-auto mt-1 text-[var(--accent-primary)] hover:underline"
          >
            {t('skills.mcp.addFirst')}
          </button>
        </div>
      ) : (
        <ScrollArea className="max-h-[200px]">
          <div className="space-y-1">
          {mcps.map((mcp) => (
            <MCPItem
              key={mcp.mcp_id}
              mcp={mcp}
              onDelete={handleDelete}
              onToggle={handleToggle}
              onValidate={handleValidate}
              validating={validatingIds.has(mcp.mcp_id)}
            />
          ))}
          </div>
        </ScrollArea>
      )}

      {/* Legend */}
      {mcps.length > 0 && (
        <div className="flex items-center gap-3 text-[9px] text-[var(--text-tertiary)] pt-1">
          <span className="flex items-center gap-1">
            <CheckCircle className="w-2.5 h-2.5 text-[var(--color-green-500)]" />
            {t('skills.mcp.connected')}
          </span>
          <span className="flex items-center gap-1">
            <XCircle className="w-2.5 h-2.5 text-[var(--color-red-500)]" />
            {t('skills.mcp.failed')}
          </span>
          <span className="flex items-center gap-1">
            <Circle className="w-2.5 h-2.5" />
            {t('skills.mcp.unknown')}
          </span>
        </div>
      )}
    </section>
  );
}
