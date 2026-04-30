/**
 * Job Template Selector - Select and configure job templates
 *
 * Features:
 * 1. Display preset template list
 * 2. Fill in variables after selecting a template
 * 3. Preview dependency graph
 * 4. Create job group
 */

import { useState, useMemo } from 'react';
import {
  Building2,
  GitPullRequest,
  Share2,
  ChevronRight,
  ArrowLeft,
  Play,
  X,
} from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent, Button, Badge, Input } from '@/components/ui';
import { cn } from '@/lib/utils';
import { JOB_TEMPLATES } from '@/stores/jobComplexStore';
import { JobDependencyGraph } from './JobDependencyGraph';
import type { JobTemplate, JobNode } from '@/types/jobComplex';

// Icon mapping
const iconMap: Record<string, typeof Building2> = {
  Building2,
  GitPullRequest,
  Share2,
};

interface JobTemplateSelectorProps {
  onCreateJobs?: (template: JobTemplate, variables: Record<string, string>) => Promise<void>;
  onClose?: () => void;
}

export function JobTemplateSelector({ onCreateJobs, onClose }: JobTemplateSelectorProps) {
  const [selectedTemplate, setSelectedTemplate] = useState<JobTemplate | null>(null);
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Validate that all required variables are filled
  const isValid = useMemo(() => {
    if (!selectedTemplate) return false;
    return selectedTemplate.variables.every(
      (v) => !v.required || (variables[v.name] && variables[v.name].trim())
    );
  }, [selectedTemplate, variables]);

  // Generate JobNode array for preview
  const previewNodes: JobNode[] = useMemo(() => {
    if (!selectedTemplate) return [];
    return selectedTemplate.jobs.map((job, index) => ({
      id: `preview_${index}`,
      task_key: job.task_key,
      title: job.title,
      description: job.description,
      status: 'pending' as const,
      depends_on: job.depends_on,
    }));
  }, [selectedTemplate]);

  // Handle variable input
  const handleVariableChange = (name: string, value: string) => {
    setVariables((prev) => ({ ...prev, [name]: value }));
    setError(null);
  };

  // Select template
  const handleSelectTemplate = (template: JobTemplate) => {
    setSelectedTemplate(template);
    // Initialize variable default values
    const defaults: Record<string, string> = {};
    template.variables.forEach((v) => {
      if (v.defaultValue) {
        defaults[v.name] = v.defaultValue;
      }
    });
    setVariables(defaults);
    setError(null);
  };

  // Return to template list
  const handleBack = () => {
    setSelectedTemplate(null);
    setVariables({});
    setError(null);
  };

  // Create Jobs
  const handleCreate = async () => {
    if (!selectedTemplate || !isValid) return;

    setIsCreating(true);
    setError(null);

    try {
      await onCreateJobs?.(selectedTemplate, variables);
      onClose?.();
    } catch (err) {
      setError(String(err));
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <Card className="w-full max-w-2xl mx-auto">
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {selectedTemplate && (
              <Button variant="ghost" size="icon" onClick={handleBack}>
                <ArrowLeft className="w-4 h-4" />
              </Button>
            )}
            <span>{selectedTemplate ? selectedTemplate.name : 'Select Template'}</span>
          </div>
          {onClose && (
            <Button variant="ghost" size="icon" onClick={onClose}>
              <X className="w-4 h-4" />
            </Button>
          )}
        </CardTitle>
      </CardHeader>

      <CardContent>
        {/* Template list */}
        {!selectedTemplate && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {JOB_TEMPLATES.map((template) => {
              const Icon = iconMap[template.icon] || Building2;
              return (
                <button
                  key={template.id}
                  onClick={() => handleSelectTemplate(template)}
                  className={cn(
                    'p-4 border rounded-lg text-left transition-all',
                    'border-[var(--border-default)]',
                    'hover:border-[var(--accent-primary)] hover:shadow-sm'
                  )}
                >
                  <div className="flex items-start gap-3">
                    <div className="p-2 rounded-lg bg-[var(--bg-elevated)]">
                      <Icon className="w-5 h-5 text-[var(--accent-primary)]" />
                    </div>
                    <div className="flex-1">
                      <div className="flex items-center justify-between">
                        <h3 className="font-medium text-[var(--text-primary)]">
                          {template.name}
                        </h3>
                        <ChevronRight className="w-4 h-4 text-[var(--text-tertiary)]" />
                      </div>
                      <p className="text-sm text-[var(--text-tertiary)] mt-1">
                        {template.description}
                      </p>
                      <div className="flex items-center gap-2 mt-2">
                        <Badge variant="default" size="sm">
                          {template.jobs.length} tasks
                        </Badge>
                        <Badge variant="default" size="sm">
                          {template.variables.length} variables
                        </Badge>
                      </div>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        )}

        {/* Template configuration */}
        {selectedTemplate && (
          <div className="space-y-6">
            {/* Variable input */}
            <div className="space-y-4">
              <h3 className="text-sm font-medium text-[var(--text-secondary)]">
                Configuration
              </h3>
              {selectedTemplate.variables.map((v) => (
                <div key={v.name} className="space-y-1">
                  <label className="text-sm text-[var(--text-secondary)]">
                    {v.label}
                    {v.required && <span className="text-[var(--color-error)]"> *</span>}
                  </label>
                  {v.type === 'select' && v.options ? (
                    <select
                      value={variables[v.name] || ''}
                      onChange={(e) => handleVariableChange(v.name, e.target.value)}
                      className={cn(
                        'w-full px-3 py-2 rounded-md border text-sm',
                        'border-[var(--border-default)] bg-[var(--bg-secondary)]',
                        'focus:border-[var(--accent-primary)] focus:outline-none'
                      )}
                    >
                      <option value="">Select...</option>
                      {v.options.map((opt) => (
                        <option key={opt} value={opt}>
                          {opt}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <Input
                      type={v.type === 'number' ? 'number' : 'text'}
                      value={variables[v.name] || ''}
                      onChange={(e) => handleVariableChange(v.name, e.target.value)}
                      placeholder={`Enter ${v.label.toLowerCase()}`}
                    />
                  )}
                </div>
              ))}
            </div>

            {/* Dependency graph preview */}
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-[var(--text-secondary)]">
                Dependency Preview
              </h3>
              <div className="h-64 border border-[var(--border-default)] rounded-lg overflow-hidden">
                <JobDependencyGraph jobs={previewNodes} />
              </div>
            </div>

            {/* Error message */}
            {error && (
              <div className="p-3 rounded-lg bg-[var(--color-error)]/10 text-[var(--color-error)] text-sm">
                {error}
              </div>
            )}

            {/* Action buttons */}
            <div className="flex items-center justify-end gap-2 pt-4 border-t border-[var(--border-subtle)]">
              <Button variant="ghost" onClick={handleBack}>
                Back
              </Button>
              <Button
                onClick={handleCreate}
                disabled={!isValid || isCreating}
                className="gap-1"
              >
                <Play className="w-4 h-4" />
                {isCreating ? 'Creating...' : 'Create Jobs'}
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
