/**
 * Workspace files panel (config drawer → Workspace section).
 *
 * Renders the agent workspace as a collapsible directory tree (dotfolders
 * are filtered server-side). Per-file actions: download, preview, register
 * as artifact, delete. Folders can be deleted (recursive). Top-level
 * drag-drop / browse upload is preserved.
 */

import { useState, useCallback, useEffect, useMemo } from 'react';
import {
  Upload,
  File as FileIcon,
  Folder,
  FolderOpen,
  Trash2,
  RefreshCw,
  ChevronRight,
  ChevronDown,
  Download,
  Eye,
  Plus,
} from 'lucide-react';
import { Button, Badge, ScrollArea, useConfirm, Dialog } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';
import { artifactsApi } from '@/services/artifactsApi';
import { cn } from '@/lib/utils';
import type { FileInfo } from '@/types';

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// ─── kind detection ───────────────────────────────────────────────────────────

const ARTIFACT_KIND_OPTIONS: { value: string; label: string }[] = [
  { value: 'text/html', label: 'HTML page / app' },
  { value: 'application/vnd.echarts+json', label: 'ECharts JSON' },
  { value: 'text/csv', label: 'CSV table' },
  { value: 'text/markdown', label: 'Markdown report' },
  { value: 'image/png', label: 'PNG image' },
  { value: 'image/jpeg', label: 'JPEG image' },
  { value: 'application/pdf', label: 'PDF document' },
];

function detectKindFromExt(name: string): string | null {
  const lower = name.toLowerCase();
  if (lower.endsWith('.html') || lower.endsWith('.htm')) return 'text/html';
  if (lower.endsWith('.csv')) return 'text/csv';
  if (lower.endsWith('.md') || lower.endsWith('.markdown')) return 'text/markdown';
  if (lower.endsWith('.png')) return 'image/png';
  if (lower.endsWith('.jpg') || lower.endsWith('.jpeg')) return 'image/jpeg';
  if (lower.endsWith('.pdf')) return 'application/pdf';
  if (lower.endsWith('.json')) return 'application/vnd.echarts+json';
  return null;
}

function stripExtension(name: string): string {
  const idx = name.lastIndexOf('.');
  return idx > 0 ? name.slice(0, idx) : name;
}

// ─── tree node ────────────────────────────────────────────────────────────────

interface TreeNodeProps {
  node: FileInfo;
  depth: number;
  onDelete: (node: FileInfo) => void;
  onPreview: (node: FileInfo) => void;
  onRegister: (node: FileInfo) => void;
  agentId: string;
  userId: string;
}

function TreeNode({
  node,
  depth,
  onDelete,
  onPreview,
  onRegister,
  agentId,
  userId,
}: TreeNodeProps) {
  const [expanded, setExpanded] = useState(depth < 1);

  const isFile = !node.is_dir;
  const downloadUrl = isFile ? api.workspaceFileRawUrl(agentId, node.path) : '';

  return (
    <div>
      <div
        className="flex items-center gap-1 px-1 py-1 rounded hover:bg-[var(--bg-tertiary)] group"
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
      >
        {node.is_dir ? (
          <button
            onClick={() => setExpanded((v) => !v)}
            className="p-0.5 opacity-60 hover:opacity-100"
            aria-label={expanded ? 'Collapse folder' : 'Expand folder'}
          >
            {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          </button>
        ) : (
          <span className="w-4" />
        )}
        {node.is_dir ? (
          expanded ? (
            <FolderOpen className="w-3.5 h-3.5 text-[var(--text-tertiary)] shrink-0" />
          ) : (
            <Folder className="w-3.5 h-3.5 text-[var(--text-tertiary)] shrink-0" />
          )
        ) : (
          <FileIcon className="w-3.5 h-3.5 text-[var(--text-tertiary)] shrink-0" />
        )}
        <div className="flex-1 min-w-0">
          <div
            className="text-xs text-[var(--text-primary)] truncate"
            title={node.path}
          >
            {node.name}
          </div>
          {!node.is_dir && (
            <div className="text-[9px] text-[var(--text-tertiary)]">
              {formatFileSize(node.size)}
            </div>
          )}
        </div>
        <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
          {isFile && (
            <>
              <a
                href={downloadUrl}
                download={node.name}
                className="w-6 h-6 flex items-center justify-center text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                title="Download"
                aria-label="Download"
              >
                <Download className="w-3 h-3" />
              </a>
              <button
                onClick={() => onPreview(node)}
                className="w-6 h-6 flex items-center justify-center text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                title="Preview"
                aria-label="Preview"
              >
                <Eye className="w-3 h-3" />
              </button>
              <button
                onClick={() => onRegister(node)}
                className="w-6 h-6 flex items-center justify-center text-[var(--text-tertiary)] hover:text-[var(--accent-primary)]"
                title="Register as artifact"
                aria-label="Register as artifact"
              >
                <Plus className="w-3 h-3" />
              </button>
            </>
          )}
          <button
            onClick={() => onDelete(node)}
            className="w-6 h-6 flex items-center justify-center text-[var(--text-tertiary)] hover:text-[var(--color-error)]"
            title={node.is_dir ? 'Delete folder (recursive)' : 'Delete'}
            aria-label="Delete"
          >
            <Trash2 className="w-3 h-3" />
          </button>
        </div>
      </div>
      {node.is_dir && expanded && node.children && node.children.length > 0 && (
        <div>
          {node.children.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              onDelete={onDelete}
              onPreview={onPreview}
              onRegister={onRegister}
              agentId={agentId}
              userId={userId}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ─── preview modal ────────────────────────────────────────────────────────────

interface PreviewModalProps {
  agentId: string;
  userId: string;
  node: FileInfo | null;
  onClose: () => void;
}

function PreviewModal({ agentId, userId, node, onClose }: PreviewModalProps) {
  const [content, setContent] = useState<
    | { type: 'text'; text: string }
    | { type: 'image'; src: string }
    | { type: 'unsupported' }
    | null
  >(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!node) {
      // setState wrapped to satisfy react-hooks/set-state-in-effect — the
      // rule treats the contents of an async IIFE as outside the effect body.
      (async () => {
        setContent(null);
        setError(null);
      })();
      return;
    }
    let cancelled = false;
    let blobUrl: string | null = null;
    (async () => {
      setContent(null);
      setError(null);
      try {
        const lower = node.name.toLowerCase();
        const isImage =
          lower.endsWith('.png') || lower.endsWith('.jpg') || lower.endsWith('.jpeg') || lower.endsWith('.gif') || lower.endsWith('.webp');
        const isText =
          lower.endsWith('.md') || lower.endsWith('.markdown') ||
          lower.endsWith('.csv') || lower.endsWith('.json') ||
          lower.endsWith('.txt') || lower.endsWith('.log') ||
          lower.endsWith('.html') || lower.endsWith('.htm') ||
          lower.endsWith('.js') || lower.endsWith('.ts') || lower.endsWith('.tsx') ||
          lower.endsWith('.css') || lower.endsWith('.py') || lower.endsWith('.yml') || lower.endsWith('.yaml');
        const blob = await api.fetchWorkspaceFileBlob(agentId, node.path);
        if (cancelled) return;
        if (isImage) {
          blobUrl = URL.createObjectURL(blob);
          setContent({ type: 'image', src: blobUrl });
        } else if (isText) {
          const text = await blob.text();
          if (!cancelled) {
            // Cap preview at 200 KB so a huge file doesn't freeze the modal.
            const cap = 200 * 1024;
            setContent({
              type: 'text',
              text:
                text.length > cap
                  ? text.slice(0, cap) + `\n\n… (truncated, file is ${formatFileSize(node.size)})`
                  : text,
            });
          }
        } else {
          setContent({ type: 'unsupported' });
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [agentId, userId, node]);

  if (!node) return null;

  return (
    <Dialog isOpen onClose={onClose} title={node.path} size="lg">
      <div className="max-h-[70vh] overflow-auto">
        {error && <div className="p-4 text-red-400 text-sm">Preview failed: {error}</div>}
        {!content && !error && <div className="p-4 opacity-60">Loading…</div>}
        {content?.type === 'image' && (
          <div className="flex items-center justify-center bg-[var(--bg-deep)] p-4">
            <img src={content.src} alt={node.name} className="max-w-full max-h-[60vh] object-contain" />
          </div>
        )}
        {content?.type === 'text' && (
          <pre className="text-xs whitespace-pre-wrap break-words p-4 font-mono">{content.text}</pre>
        )}
        {content?.type === 'unsupported' && (
          <div className="p-4 opacity-70 text-sm">
            Preview is not supported for this file type. Use the download button
            to open it locally.
          </div>
        )}
      </div>
    </Dialog>
  );
}

// ─── register modal ───────────────────────────────────────────────────────────

interface RegisterModalProps {
  agentId: string;
  node: FileInfo | null;
  onClose: () => void;
  onRegistered: () => void;
}

function RegisterModal({ agentId, node, onClose, onRegistered }: RegisterModalProps) {
  const [kind, setKind] = useState<string>('text/html');
  const [title, setTitle] = useState<string>('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!node) return;
    const detected = detectKindFromExt(node.name);
    setKind(detected ?? 'text/html');
    setTitle(stripExtension(node.name));
    setError(null);
  }, [node]);

  if (!node) return null;

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await artifactsApi.registerFromWorkspace(agentId, {
        file_path: node.path,
        kind,
        title: title.trim() || node.name,
      });
      onRegistered();
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog isOpen onClose={onClose} title="Register as artifact" size="md">
      <div className="space-y-3 p-4">
        <div className="text-xs opacity-70">
          File: <span className="font-mono">{node.path}</span>
        </div>
        <label className="block text-xs font-medium">
          Kind
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            className="mt-1 block w-full text-sm bg-[var(--bg-primary)] border border-[var(--border-default)] rounded px-2 py-1"
          >
            {ARTIFACT_KIND_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label} ({opt.value})
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs font-medium">
          Title
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="mt-1 block w-full text-sm bg-[var(--bg-primary)] border border-[var(--border-default)] rounded px-2 py-1"
            placeholder="Tab title shown next to the chat"
          />
        </label>
        {error && (
          <div className="text-xs text-[var(--color-error)] p-2 border border-[var(--color-red-500)] rounded">
            {error}
          </div>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? 'Registering…' : 'Register'}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

// ─── main component ───────────────────────────────────────────────────────────

function countNodes(tree: FileInfo[]): number {
  let n = 0;
  const walk = (nodes: FileInfo[]) => {
    for (const node of nodes) {
      n += 1;
      if (node.children) walk(node.children);
    }
  };
  walk(tree);
  return n;
}

export function FileUpload() {
  const { agentId, userId } = useConfigStore();
  const [tree, setTree] = useState<FileInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [previewNode, setPreviewNode] = useState<FileInfo | null>(null);
  const [registerNode, setRegisterNode] = useState<FileInfo | null>(null);
  const { confirm, dialog: confirmDialog } = useConfirm();

  const totalCount = useMemo(() => countNodes(tree), [tree]);

  const fetchTree = useCallback(async () => {
    if (!agentId || !userId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.listFiles(agentId);
      if (res.success) {
        setTree(res.tree);
      } else {
        setError(res.error || 'Failed to load workspace');
      }
    } catch (err) {
      setError('Failed to load workspace');
      console.error('Error fetching workspace tree:', err);
    } finally {
      setLoading(false);
    }
  }, [agentId, userId]);

  useEffect(() => {
    fetchTree();
  }, [fetchTree]);

  const handleUpload = async (filesToUpload: FileList | File[]) => {
    if (!agentId || !userId) return;
    setUploading(true);
    setError(null);
    try {
      for (const file of Array.from(filesToUpload)) {
        const res = await api.uploadFile(agentId, file);
        if (!res.success) setError(res.error || `Failed to upload ${file.name}`);
      }
      await fetchTree();
    } catch (err) {
      setError('Upload failed');
      console.error('Error uploading file:', err);
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (node: FileInfo) => {
    if (!agentId || !userId) return;
    const ok = await confirm({
      title: node.is_dir ? 'Delete folder' : 'Delete file',
      message: node.is_dir
        ? `Recursively delete folder "${node.path}" and everything inside it?`
        : `Delete "${node.path}"?`,
      confirmText: 'Delete',
      danger: true,
    });
    if (!ok) return;
    try {
      const res = await api.deleteFile(agentId, node.path);
      if (res.success) {
        await fetchTree();
      } else {
        setError(res.error || 'Failed to delete');
      }
    } catch (err) {
      setError('Delete failed');
      console.error('Error deleting workspace path:', err);
    }
  };

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);
  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  }, []);
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);
  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragging(false);
      const droppedFiles = e.dataTransfer.files;
      if (droppedFiles.length > 0) handleUpload(droppedFiles);
    },
    // handleUpload references the latest agentId/userId via closure on the
    // surrounding component; re-binding the callback per upload run isn't
    // needed because the inner function reads the current values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [agentId, userId],
  );
  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) handleUpload(e.target.files);
  };

  return (
    <section className="space-y-2">
      {confirmDialog}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)] font-medium uppercase tracking-wider">
          <FolderOpen className="w-3 h-3" />
          Workspace
        </div>
        <div className="flex items-center gap-1">
          <Badge variant="default" size="sm">{totalCount}</Badge>
          <Button
            variant="ghost"
            size="icon"
            onClick={fetchTree}
            disabled={loading}
            className="w-6 h-6"
            title="Refresh"
          >
            <RefreshCw className={cn('w-3 h-3', loading && 'animate-spin')} />
          </Button>
        </div>
      </div>

      {/* Drag and drop zone */}
      <div
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        className={cn(
          'relative border-2 border-dashed rounded-lg p-4 transition-all',
          'flex flex-col items-center justify-center gap-2',
          isDragging
            ? 'border-[var(--accent-primary)] bg-[var(--bg-elevated)]'
            : 'border-[var(--border-default)] hover:border-[var(--border-strong)]',
          uploading && 'opacity-50 pointer-events-none',
        )}
      >
        <Upload className={cn(
          'w-6 h-6',
          isDragging ? 'text-[var(--accent-primary)]' : 'text-[var(--text-tertiary)]',
        )} />
        <div className="text-center">
          <p className="text-xs text-[var(--text-secondary)]">
            {isDragging ? 'Drop files here' : 'Drag files here or'}
          </p>
          {!isDragging && (
            <label className="cursor-pointer">
              <span className="text-xs text-[var(--accent-primary)] hover:underline">
                browse
              </span>
              <input
                type="file"
                multiple
                onChange={handleFileInputChange}
                className="hidden"
              />
            </label>
          )}
        </div>
        {uploading && (
          <div className="absolute inset-0 flex items-center justify-center bg-[var(--bg-primary)]/50 rounded-lg">
            <RefreshCw className="w-5 h-5 text-[var(--accent-primary)] animate-spin" />
          </div>
        )}
      </div>

      {error && (
        <div className="text-xs text-[var(--color-error)] p-2 border border-[var(--color-red-500)]">
          {error}
        </div>
      )}

      {/* Tree */}
      {loading ? (
        <div className="space-y-1">
          <div className="animate-pulse bg-[var(--bg-secondary)] rounded h-6" />
          <div className="animate-pulse bg-[var(--bg-secondary)] rounded h-6" />
          <div className="animate-pulse bg-[var(--bg-secondary)] rounded h-6" />
        </div>
      ) : tree.length === 0 ? (
        <div className="text-xs text-[var(--text-tertiary)] text-center py-2">
          Workspace is empty
        </div>
      ) : (
        // `type="auto"` reveals the scrollbar whenever content overflows
        // (Radix default is "hover"). The previous max-h-[260px] combined
        // with the default hover-only scrollbar made users believe the
        // tree couldn't scroll at all — the bar only flashed on direct
        // hover and the wheel chained to the outer AwarenessPanel
        // ScrollArea. Pair this with overscroll-contain (default in
        // ./ui/scroll-area.tsx) so the wheel stays in the inner viewport
        // until its boundary. Bumping to max-h-[55vh] gives ~430 px on a
        // 720-line laptop / ~590 px on a 1080-line monitor before falling
        // back to inner scroll.
        <ScrollArea type="auto" className="max-h-[55vh]">
          <div className="text-xs">
            {tree.map((node) => (
              <TreeNode
                key={node.path}
                node={node}
                depth={0}
                onDelete={handleDelete}
                onPreview={setPreviewNode}
                onRegister={setRegisterNode}
                agentId={agentId || ''}
                userId={userId || ''}
              />
            ))}
          </div>
        </ScrollArea>
      )}

      <PreviewModal
        agentId={agentId || ''}
        userId={userId || ''}
        node={previewNode}
        onClose={() => setPreviewNode(null)}
      />
      <RegisterModal
        agentId={agentId || ''}
        node={registerNode}
        onClose={() => setRegisterNode(null)}
        onRegistered={() => {
          // No-op for the tree (the workspace files didn't change), but the
          // artifact panel will pick up the new artifact via its own refresh.
        }}
      />
    </section>
  );
}
