/**
 * @file_name: InstallDialog.tsx
 * @author: Bin Liang
 * @date: 2026-03-06
 * @description: Dialog for installing skills from GitHub or zip files
 */

import { useState } from 'react';
import {
  Github,
  FileArchive,
  Loader2,
  AlertCircle,
  CheckCircle,
  X,
  Plus,
} from 'lucide-react';
import { Button } from '@/components/ui';
import { cn } from '@/lib/utils';

export type InstallMode = 'github' | 'zip' | null;

export function InstallDialog({
  mode,
  onClose,
  onInstall,
  isInstalling,
}: {
  mode: InstallMode;
  onClose: () => void;
  onInstall: (data: { url?: string; branch?: string; file?: File }) => void;
  isInstalling: boolean;
}) {
  const [url, setUrl] = useState('');
  const [branch, setBranch] = useState('main');
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (mode === 'github') {
      if (!url.trim()) {
        setError('GitHub URL is required');
        return;
      }
      if (!/^https:\/\/github\.com\/.+/i.test(url.trim()) && !/^github:[^/]+\/.+/i.test(url.trim())) {
        setError('Only GitHub repositories are supported');
        return;
      }
      onInstall({ url: url.trim(), branch: branch.trim() || 'main' });
    } else if (mode === 'zip') {
      if (!file) {
        setError('Please select a zip file');
        return;
      }
      onInstall({ file });
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0];
    if (selectedFile) {
      if (!selectedFile.name.endsWith('.zip')) {
        setError('Please select a .zip file');
        return;
      }
      setFile(selectedFile);
      setError(null);
    }
  };

  if (!mode) return null;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 animate-fade-in">
      <div className="bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-2xl p-6 w-full max-w-md shadow-2xl">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-[var(--text-primary)] flex items-center gap-2">
            {mode === 'github' ? (
              <>
                <Github className="w-5 h-5" />
                Install from GitHub
              </>
            ) : (
              <>
                <FileArchive className="w-5 h-5" />
                Install from Zip
              </>
            )}
          </h3>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <X className="w-4 h-4 text-[var(--text-tertiary)]" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* GitHub URL */}
          {mode === 'github' && (
            <>
              <div>
                <label className="block text-xs font-medium text-[var(--text-secondary)] mb-2">
                  GitHub URL
                </label>
                <input
                  type="text"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://github.com/username/skill-repo"
                  className="w-full px-4 py-2.5 rounded-xl bg-[var(--bg-sunken)] border border-[var(--border-subtle)] text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)] transition-colors"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-[var(--text-secondary)] mb-2">
                  Branch (optional)
                </label>
                <input
                  type="text"
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                  placeholder="main"
                  className="w-full px-4 py-2.5 rounded-xl bg-[var(--bg-sunken)] border border-[var(--border-subtle)] text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-[var(--accent-primary)] transition-colors"
                />
              </div>
            </>
          )}

          {/* Zip file upload */}
          {mode === 'zip' && (
            <div>
              <label className="block text-xs font-medium text-[var(--text-secondary)] mb-2">
                Skill Package (.zip)
              </label>
              <div
                className={cn(
                  'relative border-2 border-dashed rounded-xl p-6 transition-colors cursor-pointer',
                  file
                    ? 'border-[var(--color-success)] bg-[var(--color-success)]/5'
                    : 'border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50'
                )}
              >
                <input
                  type="file"
                  accept=".zip"
                  onChange={handleFileChange}
                  className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                />
                <div className="flex flex-col items-center justify-center gap-2 text-center">
                  {file ? (
                    <>
                      <CheckCircle className="w-8 h-8 text-[var(--color-success)]" />
                      <span className="text-sm text-[var(--text-primary)]">{file.name}</span>
                      <span className="text-xs text-[var(--text-tertiary)]">
                        {(file.size / 1024).toFixed(1)} KB
                      </span>
                    </>
                  ) : (
                    <>
                      <FileArchive className="w-8 h-8 text-[var(--text-tertiary)]" />
                      <span className="text-sm text-[var(--text-secondary)]">
                        Click or drag to upload
                      </span>
                      <span className="text-xs text-[var(--text-tertiary)]">
                        .zip files only
                      </span>
                    </>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--color-error)]/10 border border-[var(--color-error)]/20">
              <AlertCircle className="w-4 h-4 text-[var(--color-error)]" />
              <span className="text-xs text-[var(--color-error)]">{error}</span>
            </div>
          )}

          <div className="rounded-lg border border-[var(--color-warning)]/20 bg-[var(--color-warning)]/8 px-3 py-2">
            <p className="text-xs text-[var(--text-secondary)]">
              Only install skills from sources you trust. A skill can contain docs, prompts, and scripts the agent may read or use.
            </p>
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <Button
              type="button"
              variant="ghost"
              onClick={onClose}
              disabled={isInstalling}
              className="flex-1"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="default"
              disabled={isInstalling}
              className="flex-1"
            >
              {isInstalling ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Installing...
                </>
              ) : (
                <>
                  <Plus className="w-4 h-4 mr-2" />
                  Install
                </>
              )}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
