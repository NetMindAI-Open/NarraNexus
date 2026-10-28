/**
 * Job Complex type definitions
 * Used for dependency graph, timeline, and template system
 */

// Job node type (used for dependency graph)
export type JobNodeStatus = 'pending' | 'active' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface JobNode {
  id: string;           // job_id or instance_id
  task_key: string;     // Task identifier (used for dependency references)
  title: string;        // Display name
  description?: string; // Description
  status: JobNodeStatus;
  depends_on: string[]; // List of dependent task_keys
  started_at?: string;
  completed_at?: string;
  output?: string;      // Output summary
}

// Job Complex group response
export interface JobComplexResponse {
  success: boolean;
  group_id: string;
  jobs: JobNode[];
  error?: string;
}

// Create Job Complex request (frontend to backend)
export interface JobComplexJobRequest {
  task_key: string;
  title: string;
  description?: string;
  depends_on: string[];
  payload?: string;
}

export interface CreateJobComplexRequest {
  agent_id: string;
  // No user_id: the backend derives identity from the authenticated request,
  // never the body (a body-supplied user_id was the create_job_complex IDOR).
  group_id?: string;
  jobs: JobComplexJobRequest[];
}

export interface CreateJobComplexResponse {
  success: boolean;
  group_id?: string;
  jobs_created?: number;
  job_ids?: string[];
  error?: string;
}

// Timeline event
export interface TimelineEvent {
  jobId: string;
  title: string;
  status: JobNodeStatus;
  startTime: Date;
  endTime?: Date;
}

// Template definition
export interface JobTemplateVariable {
  name: string;
  label: string;
  type: 'text' | 'select' | 'number';
  options?: string[];
  required: boolean;
  defaultValue?: string;
}

export interface JobTemplateJob {
  task_key: string;
  title: string;
  description: string;
  depends_on: string[];
  payload_template: string;  // Supports {{variable}} variable substitution
}

export interface JobTemplate {
  id: string;
  name: string;
  description: string;
  icon: string;
  jobs: JobTemplateJob[];
  variables: JobTemplateVariable[];
}
