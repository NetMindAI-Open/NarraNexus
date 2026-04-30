/**
 * RAG Upload Component - Upload files to Gemini RAG store with status tracking
 * Supports: .txt, .md, .pdf only (docling disabled)
 */

import { useState, useCallback, useEffect } from 'react';
import { Upload, Trash2, RefreshCw, Database, CheckCircle, AlertCircle, Loader2, FileText, X } from 'lucide-react';
import { Button, Badge, ScrollArea, useConfirm } from '@/components/ui';
import { useConfigStore, usePreloadStore } from '@/stores';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import type { RAGFileInfo } from '@/types';

// Supported file formats
const SUPPORTED_EXTENSIONS = ['.txt', '.md', '.pdf'];
const ACCEPT_STRING = SUPPORTED_EXTENSIONS.join(',');

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function getStatusIcon(status: RAGFileInfo['upload_status']) {
  switch (status) {
    case 'completed':
      return <CheckCircle className="w-3.5 h-3.5 text-[var(--color-green-500)]" />;
    case 'failed':
      return <AlertCircle className="w-3.5 h-3.5 text-[var(--color-red-500)]" />;
    case 'uploading':
      return <Loader2 className="w-3.5 h-3.5 text-[var(--color-blue-500)] animate-spin" />;
    case 'pending':
    default:
      return <Loader2 className="w-3.5 h-3.5 text-[var(--color-yellow-500)]" />;
  }
}

function getStatusColor(status: RAGFileInfo['upload_status']) {
  switch (status) {
    case 'completed':
      return 'border-[var(--color-green-500)]';
    case 'failed':
      return 'border-[var(--color-red-500)]';
    case 'uploading':
      return 'border-[var(--color-blue-500)]';
    case 'pending':
    default:
      return 'border-[var(--color-yellow-500)]';
  }
}

export function RAGUpload() {
  const { agentId, userId } = useConfigStore();
  const {
    ragFiles,
    ragCompletedCount,
    ragPendingCount,
    ragFilesLoading,
    ragFilesError,
    refreshRAGFiles
  } = usePreloadStore();

  const [uploading, setUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { confirm, dialog: confirmDialog } = useConfirm();

  // Text input modal state
  const [showTextModal, setShowTextModal] = useState(false);
  const [textContent, setTextContent] = useState('');
  const [textFilename, setTextFilename] = useState('');

  // Auto-refresh when there are pending files
  useEffect(() => {
    if (!agentId || !userId) return;

    const hasPendingOrUploading = ragFiles.some(
      f => f.upload_status === 'pending' || f.upload_status === 'uploading'
    );

    if (hasPendingOrUploading) {
      const interval = setInterval(() => {
        refreshRAGFiles(agentId, userId);
      }, 3000); // Poll every 3 seconds

      return () => clearInterval(interval);
    }
  }, [agentId, userId, ragFiles, refreshRAGFiles]);

  // Fetch files on mount
  const fetchFiles = useCallback(async () => {
    if (!agentId || !userId) return;
    setError(null);
    await refreshRAGFiles(agentId, userId);
  }, [agentId, userId, refreshRAGFiles]);

  // Validate file extension
  const isValidFileType = (filename: string): boolean => {
    const lowerName = filename.toLowerCase();
    return SUPPORTED_EXTENSIONS.some(ext => lowerName.endsWith(ext));
  };

  // Handle file upload
  const handleUpload = async (filesToUpload: FileList | File[]) => {
    if (!agentId || !userId) return;

    setUploading(true);
    setError(null);

    try {
      for (const file of Array.from(filesToUpload)) {
        // Frontend file format validation
        if (!isValidFileType(file.name)) {
          setError(`Unsupported file format: ${file.name}. Only ${SUPPORTED_EXTENSIONS.join(', ')} are supported.`);
          continue;
        }

        const res = await api.uploadRAGFile(agentId, userId, file);
        if (!res.success) {
          setError(res.error || `Failed to upload ${file.name}`);
        }
      }
      // Refresh file list
      await fetchFiles();
    } catch (err) {
      setError('Upload failed');
      console.error('Error uploading RAG file:', err);
    } finally {
      setUploading(false);
    }
  };

  // Handle text content upload
  const handleTextUpload = async () => {
    if (!agentId || !userId || !textContent.trim()) return;

    setUploading(true);
    setError(null);

    try {
      // Generate filename
      const filename = textFilename.trim()
        ? (textFilename.endsWith('.txt') ? textFilename : `${textFilename}.txt`)
        : `text_${Date.now()}.txt`;

      // Create text file
      const blob = new Blob([textContent], { type: 'text/plain' });
      const file = new File([blob], filename, { type: 'text/plain' });

      const res = await api.uploadRAGFile(agentId, userId, file);
      if (res.success) {
        setShowTextModal(false);
        setTextContent('');
        setTextFilename('');
        await fetchFiles();
      } else {
        setError(res.error || 'Failed to upload text');
      }
    } catch (err) {
      setError('Upload failed');
      console.error('Error uploading text:', err);
    } finally {
      setUploading(false);
    }
  };

  // Handle file deletion
  const handleDelete = async (filename: string) => {
    if (!agentId || !userId) return;
    const ok = await confirm({
      title: 'Delete file',
      message: `Delete ${filename}?\n\nNote: This will remove the local file but the content may still be in the RAG store.`,
      confirmText: 'Delete',
      danger: true,
    });
    if (!ok) return;

    try {
      const res = await api.deleteRAGFile(agentId, userId, filename);
      if (res.success) {
        await fetchFiles();
      } else {
        setError(res.error || 'Failed to delete file');
      }
    } catch (err) {
      setError('Delete failed');
      console.error('Error deleting RAG file:', err);
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
      // Filter valid files
      const validFiles = Array.from(droppedFiles).filter(f => isValidFileType(f.name));
      if (validFiles.length === 0) {
        setError(`Unsupported file format. Only ${SUPPORTED_EXTENSIONS.join(', ')} are supported.`);
        return;
      }
      if (validFiles.length < droppedFiles.length) {
        setError(`Some files were skipped (unsupported format). Only ${SUPPORTED_EXTENSIONS.join(', ')} are supported.`);
      }
      handleUpload(validFiles);
    }
  }, [agentId, userId]);

  // File input change handler
  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      handleUpload(e.target.files);
    }
  };

  const totalCount = ragFiles.length;

  return (
    <section className="space-y-2">
      {confirmDialog}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)] font-medium uppercase tracking-wider">
          <Database className="w-3 h-3" />
          RAG Knowledge Base
        </div>
        <div className="flex items-center gap-1">
          {ragCompletedCount > 0 && (
            <Badge variant="success" size="sm" title="Completed">
              {ragCompletedCount}
            </Badge>
          )}
          {ragPendingCount > 0 && (
            <Badge variant="warning" size="sm" title="Pending/Uploading">
              {ragPendingCount}
            </Badge>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={fetchFiles}
            disabled={ragFilesLoading}
            className="w-6 h-6"
            title="Refresh"
          >
            <RefreshCw className={cn('w-3 h-3', ragFilesLoading && 'animate-spin')} />
          </Button>
        </div>
      </div>

      {/* Upload Buttons Row */}
      <div className="flex gap-2">
        {/* File Upload Button */}
        <label className={cn(
          'flex-1 flex items-center justify-center gap-2 py-2 px-3 rounded-lg cursor-pointer',
          'border border-dashed border-[var(--border-default)] bg-[var(--bg-secondary)]',
          'hover:border-[var(--accent-primary)] hover:bg-[var(--bg-tertiary)] transition-all',
          uploading && 'opacity-50 pointer-events-none'
        )}>
          <Upload className="w-4 h-4 text-[var(--text-tertiary)]" />
          <span className="text-xs text-[var(--text-secondary)]">Upload File</span>
          <input
            type="file"
            multiple
            onChange={handleFileInputChange}
            className="hidden"
            accept={ACCEPT_STRING}
          />
        </label>

        {/* Text Input Button */}
        <Button
          variant="outline"
          size="sm"
          onClick={() => setShowTextModal(true)}
          disabled={uploading}
          className="flex items-center gap-2"
        >
          <FileText className="w-4 h-4" />
          <span>Text</span>
        </Button>
      </div>

      {/* Drag and Drop Zone */}
      <div
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        className={cn(
          'relative border-2 border-dashed rounded-lg p-3 transition-all',
          'flex flex-col items-center justify-center gap-1',
          isDragging
            ? 'border-[var(--accent-primary)] bg-[var(--accent-glow)]'
            : 'border-[var(--border-subtle)] hover:border-[var(--border-default)]',
          uploading && 'opacity-50 pointer-events-none'
        )}
      >
        <Upload className={cn(
          'w-5 h-5',
          isDragging ? 'text-[var(--accent-primary)]' : 'text-[var(--text-tertiary)]'
        )} />
        <p className="text-xs text-[var(--text-secondary)]">
          {isDragging ? 'Drop files here' : 'Or drag files here'}
        </p>
        <p className="text-[9px] text-[var(--text-tertiary)]">
          Supports: {SUPPORTED_EXTENSIONS.join(', ')}
        </p>
        {uploading && (
          <div className="absolute inset-0 flex items-center justify-center bg-[var(--bg-primary)]/50 rounded-lg">
            <RefreshCw className="w-5 h-5 text-[var(--accent-primary)] animate-spin" />
          </div>
        )}
      </div>

      {/* Text Input Modal */}
      {showTextModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(17,18,20,0.6)]">
          <div className="bg-[var(--bg-primary)] border border-[var(--text-primary)] w-full max-w-lg mx-4 overflow-hidden">
            {/* Modal Header */}
            <div className="flex items-center justify-between p-4 border-b border-[var(--border-subtle)]">
              <div className="flex items-center gap-2">
                <FileText className="w-5 h-5 text-[var(--accent-primary)]" />
                <h3 className="text-sm font-medium text-[var(--text-primary)]">Add Text to RAG</h3>
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => {
                  setShowTextModal(false);
                  setTextContent('');
                  setTextFilename('');
                }}
                className="w-8 h-8"
              >
                <X className="w-4 h-4" />
              </Button>
            </div>

            {/* Modal Body */}
            <div className="p-4 space-y-3">
              <div>
                <label className="text-xs text-[var(--text-secondary)] mb-1 block">
                  Filename (optional)
                </label>
                <input
                  type="text"
                  value={textFilename}
                  onChange={(e) => setTextFilename(e.target.value)}
                  placeholder="my_document.txt"
                  className="w-full px-3 py-2 text-sm bg-[var(--bg-sunken)] border border-[var(--border-default)] rounded-lg text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)]"
                />
              </div>
              <div>
                <label className="text-xs text-[var(--text-secondary)] mb-1 block">
                  Text Content
                </label>
                <textarea
                  value={textContent}
                  onChange={(e) => setTextContent(e.target.value)}
                  placeholder="Enter or paste your text content here..."
                  rows={8}
                  className="w-full px-3 py-2 text-sm bg-[var(--bg-sunken)] border border-[var(--border-default)] rounded-lg text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)] resize-none font-mono"
                />
              </div>
            </div>

            {/* Modal Footer */}
            <div className="flex items-center justify-end gap-2 p-4 border-t border-[var(--border-subtle)] bg-[var(--bg-secondary)]">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setShowTextModal(false);
                  setTextContent('');
                  setTextFilename('');
                }}
              >
                Cancel
              </Button>
              <Button
                variant="accent"
                size="sm"
                onClick={handleTextUpload}
                disabled={!textContent.trim() || uploading}
              >
                {uploading ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin mr-1" />
                    Uploading...
                  </>
                ) : (
                  <>
                    <Upload className="w-4 h-4 mr-1" />
                    Add to RAG
                  </>
                )}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Error Message */}
      {(error || ragFilesError) && (
        <div className="text-xs text-[var(--color-red-500)] p-2 border border-[var(--color-red-500)]">
          {error || ragFilesError}
        </div>
      )}

      {/* File List */}
      {ragFilesLoading && ragFiles.length === 0 ? (
        <div className="space-y-1">
          <div className="animate-pulse bg-[var(--bg-secondary)] rounded h-8" />
          <div className="animate-pulse bg-[var(--bg-secondary)] rounded h-8" />
        </div>
      ) : totalCount === 0 ? (
        <div className="text-xs text-[var(--text-tertiary)] text-center py-2">
          No RAG files uploaded yet
        </div>
      ) : (
        <ScrollArea className="max-h-[200px]">
          <div className="space-y-1">
          {ragFiles.map((file) => (
            <div
              key={file.filename}
              className={cn(
                'flex items-center gap-2 p-2 rounded group transition-colors border',
                getStatusColor(file.upload_status)
              )}
            >
              {getStatusIcon(file.upload_status)}
              <div className="flex-1 min-w-0">
                <div className="text-xs text-[var(--text-primary)] truncate" title={file.filename}>
                  {file.filename}
                </div>
                <div className="flex items-center gap-2 text-[9px] text-[var(--text-tertiary)]">
                  <span>{formatFileSize(file.size)}</span>
                  {file.error_message && (
                    <span className="text-[var(--color-red-500)] truncate" title={file.error_message}>
                      {file.error_message}
                    </span>
                  )}
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

      {/* Status Summary */}
      {totalCount > 0 && (
        <div className="text-[9px] text-[var(--text-tertiary)] text-center">
          {ragCompletedCount} of {totalCount} files indexed
          {ragPendingCount > 0 && ' (indexing in progress...)'}
        </div>
      )}
    </section>
  );
}
