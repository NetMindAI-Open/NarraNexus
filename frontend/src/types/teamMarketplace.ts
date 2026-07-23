/**
 * @file_name: teamMarketplace.ts
 * @author: NetMind.AI
 * @date: 2026-07-21
 * @description: Types for the Team Marketplace (team/agent bundle templates).
 */

export interface TeamTemplate {
  template_id: string;
  name: string;
  description: string;
  categories: string[];
  author: string;
  agent_count: number;
  thumbnail_url?: string | null;
  store_key?: string;
  bundle_sha256?: string;
  enabled?: boolean;
  sort_order?: number;
  downloads?: number;
}

export interface TeamMarketplaceListResponse {
  templates: TeamTemplate[];
}
