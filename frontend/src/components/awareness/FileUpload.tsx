/**
 * File Upload Component - Drag and drop file upload for agent workspace
 */

import { useState, useCallback, useEffect } from 'react';
import { Upload, File, Trash2, RefreshCw, FolderOpen } from 'lucide-react';
import { Button, Badge, ScrollArea, useConfirm } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import type { FileInfo } from '@/types';

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

export function FileUpload() {
  const { agentId, userId } = useConfigStore();
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { confirm, dialog: confirmDialog } = useConfirm();

  // Fetch files on mount and when agent/user changes
  const fetchFiles = useCallback(async () => {
    if (!agentId || !userId) return;

    setLoading(true);
    setError(null);
    try {
      const res = await api.listFiles(agentId, userId);
      if (res.success) {
        setFiles(res.files);
      } else {
        setError(res.error || 'Failed to load files');
      }
    } catch (err) {
      setError('Failed to load files');
      console.error('Error fetching files:', err);
    } finally {
      setLoading(false);
    }
  }, [agentId, userId]);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles]);

  // Handle file upload
  const handleUpload = async (filesToUpload: FileList | File[]) => {
    if (!agentId || !userId) return;

    setUploading(true);
    setError(null);

    try {
      for (const file of Array.from(filesToUpload)) {
        const res = await api.uploadFile(agentId, userId, file);
        if (!res.success) {
          setError(res.error || `Failed to upload ${file.name}`);
        }
      }
      // Refresh file list
      await fetchFiles();
    } catch (err) {
      setError('Upload failed');
      console.error('Error uploading file:', err);
    } finally {
      setUploading(false);
    }
  };

  // Handle file deletion
  const handleDelete = async (filename: string) => {
    if (!agentId || !userId) return;
    const ok = await confirm({
      title: 'Delete file',
      message: `Delete ${filename}?`,
      confirmText: 'Delete',
      danger: true,
    });
    if (!ok) return;

    try {
      const res = await api.deleteFile(agentId, userId, filename);
      if (res.success) {
        setFiles(files.filter(f => f.filename !== filename));
      } else {
        setError(res.error || 'Failed to delete file');
      }
    } catch (err) {
      setError('Delete failed');
      console.error('Error deleting file:', err);
    }
  };

  // Drag and drop handlers
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

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    const droppedFiles = e.dataTransfer.files;
    if (droppedFiles.length > 0) {
      handleUpload(droppedFiles);
    }
  }, [agentId, userId]);

  // File input change handler
  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      handleUpload(e.target.files);
    }
  };

  return (
    <section className="space-y-2">
      {confirmDialog}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)] font-medium uppercase tracking-wider">
          <FolderOpen className="w-3 h-3" />
          Workspace Files
        </div>
        <div className="flex items-center gap-1">
          <Badge variant="default" size="sm">{files.length}</Badge>
          <Button
            variant="ghost"
            size="icon"
            onClick={fetchFiles}
            disabled={loading}
            className="w-6 h-6"
            title="Refresh"
          >
            <RefreshCw className={cn('w-3 h-3', loading && 'animate-spin')} />
          </Button>
        </div>
      </div>

      {/* Drag and Drop Zone */}
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
          uploading && 'opacity-50 pointer-events-none'
        )}
      >
        <Upload className={cn(
          'w-6 h-6',
          isDragging ? 'text-[var(--accent-primary)]' : 'text-[var(--text-tertiary)]'
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

      {/* Error Message */}
      {error && (
        <div className="text-xs text-[var(--color-error)] p-2 border border-[var(--color-red-500)]">
          {error}
        </div>
      )}

      {/* File List */}
      {loading ? (
        <div className="space-y-1">
          <div className="animate-pulse bg-[var(--bg-secondary)] rounded h-8" />
          <div className="animate-pulse bg-[var(--bg-secondary)] rounded h-8" />
        </div>
      ) : files.length === 0 ? (
        <div className="text-xs text-[var(--text-tertiary)] text-center py-2">
          No files uploaded yet
        </div>
      ) : (
        <ScrollArea className="max-h-[150px]">
          <div className="space-y-1">
          {files.map((file) => (
            <div
              key={file.filename}
              className="flex items-center gap-2 p-2 bg-[var(--bg-secondary)] rounded group hover:bg-[var(--bg-tertiary)] transition-colors"
            >
              <File className="w-3.5 h-3.5 text-[var(--text-tertiary)] shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-xs text-[var(--text-primary)] truncate" title={file.filename}>
                  {file.filename}
                </div>
                <div className="text-[9px] text-[var(--text-tertiary)]">
                  {formatFileSize(file.size)}
                </div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => handleDelete(file.filename)}
                className="w-6 h-6 opacity-0 group-hover:opacity-100 transition-opacity text-[var(--text-tertiary)] hover:text-[var(--color-error)]"
                title="Delete"
              >
                <Trash2 className="w-3 h-3" />
              </Button>
            </div>
          ))}
          </div>
        </ScrollArea>
      )}
    </section>
  );
}
