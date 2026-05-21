/**
 * BundleExportPage — Subproject 2 Export Wizard.
 *
 * Layout:
 *   ┌────────────────────────────────────────────────────────────────┐
 *   │  Step:  Agents → Skills → Social Network → Workspace files     │
 *   ├────────────────────────────────────────────────────────────────┤
 *   │ active tab content                                              │
 *   ├────────────────────────────────────────────────────────────────┤
 *   │ Bundle Notes (markdown editor — optional)                      │
 *   ├────────────────────────────────────────────────────────────────┤
 *   │ [< back to settings]    [continue → review summary]            │
 *   └────────────────────────────────────────────────────────────────┘
 *
 * Final step is a Review Summary modal that shows everything that's
 * about to be packaged + warnings + the explicit "free text not auto-scanned"
 * notice. User clicks Download to actually trigger the export.
 */

import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ArrowLeft,
  AlertTriangle,
  Check,
  Download,
  FileText,
  Loader2,
  Package,
  Search,
  Users,
  Wrench,
  ListTree,
  Hexagon,
  Radio,
  Sparkles,
  Server,
} from 'lucide-react';
import { Button, useConfirm } from '@/components/ui';
import { BracketSectionLabel } from '@/components/nm';
import { useConfigStore, useTeamsStore } from '@/stores';
import { api } from '@/lib/api';
import { cn } from '@/lib/utils';
import type {
  BundleExportRequest,
  SkillExportSpec,
  SkillArchiveRecord,
  TeamWithMembers,
  BundleArtifactPreview,
  BundleMcpPreview,
} from '@/types';

const TABS: { id: TabId; label: string; icon: any }[] = [
  { id: 'agents', label: 'Agents', icon: Users },
  { id: 'history', label: 'Narratives & Events', icon: FileText },
  // Skills sidebar in-app merges Skills + MCP into one Card; the wizard
  // follows the same grouping so users see one consistent surface for
  // "agent tools" no matter whether they're managing or packaging.
  { id: 'skills', label: 'Skills & MCP', icon: Wrench },
  { id: 'social', label: 'Social Network', icon: Hexagon },
  { id: 'bus', label: 'Message Bus', icon: Radio },
  // Artifacts ride along inside workspace.tar.gz already; this tab controls
  // whether the DB pointer rows ship so the recipient sees the artifact in
  // their Settings → Artifacts table after import.
  { id: 'artifacts', label: 'Artifacts', icon: Sparkles },
  { id: 'workspace', label: 'Workspace files', icon: ListTree },
];

type TabId = 'agents' | 'history' | 'skills' | 'social' | 'bus' | 'artifacts' | 'workspace';

interface SocialEntity {
  entity_id: string;
  entity_type: string;
  entity_name?: string | null;
  entity_description?: string | null;
  tags?: string[] | null;
}

interface ChatHistoryEvent {
  event_id: string;
  trigger?: string;
  created_at?: string;
  preview?: string;
}

interface ChatHistoryJob {
  job_id: string;
  title?: string;
  description?: string;
  status?: string;
  job_type?: string;
}

interface BusChannel {
  channel_id: string;
  name: string;
  channel_type: string;
  in_closure_member_ids: string[];
  all_member_ids: string[];
  message_count: number;
  created_at?: string | null;
}

interface ChatHistoryNarrative {
  narrative_id: string;
  // human-readable label — derived from API's `name` (preferred) or first event preview
  title: string;
  description?: string;
  current_summary?: string;
  type?: string;
  instances_count?: number;
  events: ChatHistoryEvent[];
  jobs: ChatHistoryJob[];      // P7: jobs grouped by parent narrative
  created_at?: string;
}

export default function BundleExportPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { agents, userId } = useConfigStore();
  const { teams, refresh: refreshTeams } = useTeamsStore();
  const { alert, confirm: confirmDialog, dialog } = useConfirm();

  const [tab, setTab] = useState<TabId>('agents');
  // PRD §5 议题 2 — Full vs Custom export mode (PRD names: Full = 1:1 snapshot
  // for self-backup; Custom = pick-and-choose for sharing).
  const [mode, setMode] = useState<'full' | 'custom'>('custom');
  const [selectedAgents, setSelectedAgents] = useState<Set<string>>(new Set());
  const [selectedTeam, setSelectedTeam] = useState<string>('');
  const [introMd, setIntroMd] = useState('');
  const [includeChat, setIncludeChat] = useState(true);

  // Skills state. Each agent's skills are stored as objects (not strings)
  // because SkillInfo.name comes from SKILL.md frontmatter and CAN duplicate
  // across two physically-different skill dirs (e.g. user installed `arena`
  // twice into different folders). The DIRECTORY NAME (= basename of path)
  // is filesystem-unique within one agent's skills/ dir, so we key state
  // by (agent_id, dirName) — and only show `name` in the UI.
  type SkillEntry = { name: string; dirName: string; path: string };
  const [skillsForAgents, setSkillsForAgents] = useState<Record<string, SkillEntry[]>>({});
  const [skillArchives, setSkillArchives] = useState<SkillArchiveRecord[]>([]);
  const [skillChoices, setSkillChoices] = useState<Record<string, SkillExportSpec>>({});

  const skillKey = (agentId: string, dirName: string) => `${agentId}::${dirName}`;

  // Social state
  const [socialEntities, setSocialEntities] = useState<Record<string, SocialEntity[]>>({});
  const [socialSelected, setSocialSelected] = useState<Record<string, Set<string>>>({});
  const [socialPage, setSocialPage] = useState<Record<string, number>>({});
  // Right-pane (selected entities) pagination — independent from left.
  const [socialSelectedPage, setSocialSelectedPage] = useState<Record<string, number>>({});

  // Workspace state
  const [workspaceFiles, setWorkspaceFiles] = useState<Record<string, { path: string; size: number; sensitive: boolean }[]>>({});
  const [workspaceExcludes, setWorkspaceExcludes] = useState<Record<string, Set<string>>>({});

  // Narrative selection state — narrative-level allowlist (per agent)
  // and event-level allowlist (per narrative).
  //
  // The two selections use OPPOSITE defaults on purpose:
  //
  // - Narratives are an "exclusion set": every narrative is included
  //   by default; user-unchecked narrative_ids land in the set. This
  //   matches the "narratives are the bundle's skeleton" intuition —
  //   you usually want all of them shipped.
  // - Events are an "inclusion set": every event starts UNchecked;
  //   only event_ids the user explicitly opted-in land in the set.
  //   This was flipped on 2026-05-18 after Bin哥's feedback: "选中
  //   narrative 时，默认不选任何 events"。Chat content is the most
  //   sensitive thing in the bundle, so default = opt-in.
  const [historyByAgent, setHistoryByAgent] = useState<Record<string, ChatHistoryNarrative[]>>({});
  const [excludedNarratives, setExcludedNarratives] = useState<Record<string, Set<string>>>({});
  const [includedEvents, setIncludedEvents] = useState<Record<string, Set<string>>>({});
  // P7: per-narrative job exclusion (default = include). When the parent
  // narrative is excluded, its jobs are auto-dropped on the backend (P4)
  // regardless of this set.
  const [excludedJobs, setExcludedJobs] = useState<Record<string, Set<string>>>({});

  // Per-narrative "how many events to render checkboxes for" cap.
  // Default 30. UI exposes "Show 30 more" + "Show all" buttons to grow
  // this value when a narrative has many events. This is *render-only*
  // pagination — all events for the narrative are already loaded
  // (getChatHistory called with event_limit=0), and the "Select all
  // events" / "No events" buttons act on the full list regardless of
  // the display cap. Stored separately from the data so changing the
  // cap doesn't refetch.
  const EVENT_PAGE_SIZE = 30;
  const [eventDisplayLimit, setEventDisplayLimit] = useState<Record<string, number>>({});

  // Message Bus channels available for the selected agent closure. Lazy-fetched
  // when (a) the user opens the Bus tab, or (b) selectedAgents changes. Default
  // selection = all candidates (matches legacy auto-include behavior).
  // `null` = not yet fetched (loading), `[]` = fetched and empty.
  const [busChannels, setBusChannels] = useState<BusChannel[] | null>(null);
  const [busSelected, setBusSelected] = useState<Set<string>>(new Set());
  // We track whether the user has manually edited the selection. If they
  // haven't, agent-closure changes auto-refresh defaults; if they have, we
  // preserve their picks across re-fetches.
  const [busSelectionTouched, setBusSelectionTouched] = useState(false);

  // Artifacts available per agent (lazy-fetched when artifacts tab is opened
  // or closure changes). null = pending; [] = loaded empty.
  const [artifactsForAgents, setArtifactsForAgents] = useState<Record<string, BundleArtifactPreview[] | null>>({});
  // Per-agent artifact selection. Default = all artifacts selected; user can
  // untick individuals. Same Set semantics as socialSelected.
  const [artifactSelected, setArtifactSelected] = useState<Record<string, Set<string>>>({});

  // MCP URLs available per agent (lazy-fetched same as artifacts).
  const [mcpsForAgents, setMcpsForAgents] = useState<Record<string, BundleMcpPreview[] | null>>({});
  // Per-agent MCP selection. Default = NONE selected (MCP is opt-in by design
  // — URLs frequently point at private services the bundle author may not
  // want to redistribute by accident).
  const [mcpSelected, setMcpSelected] = useState<Record<string, Set<string>>>({});

  // P9: bundle filename — defaults to <team_name>-YYYYMMDD.nxbundle when a
  // team is selected, else bundle-<YYYYMMDD>.nxbundle. Editable.
  const [filename, setFilename] = useState<string>('');

  // B6: sensitive zip confirmation flag (set after user confirms)
  const [acceptSensitiveZips, setAcceptSensitiveZips] = useState(false);
  const [sensitiveHits, setSensitiveHits] = useState<{ skill: string; hits: string[] }[] | null>(null);

  const [reviewing, setReviewing] = useState(false);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => { refreshTeams(); }, [refreshTeams]);
  useEffect(() => {
    api.listSkillArchives().then((r) => setSkillArchives(r.archives)).catch(() => {});
  }, []);

  // Quick-launch from TeamDetailPage: ?team=<team_id>&agents=<csv>
  useEffect(() => {
    const teamFromQuery = searchParams.get('team');
    const agentsFromQuery = searchParams.get('agents');
    if (teamFromQuery) setSelectedTeam(teamFromQuery);
    if (agentsFromQuery) {
      const ids = agentsFromQuery.split(',').filter(Boolean);
      if (ids.length > 0) setSelectedAgents(new Set(ids));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // P9: derive default filename from selected team. User can still type over
  // it. We only auto-fill when the field is empty OR matches a previous
  // auto-fill (i.e. the user hasn't typed anything custom).
  useEffect(() => {
    const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    const team = teams.find((t) => t.team.team_id === selectedTeam);
    const safeName = team
      ? team.team.name.replace(/[^a-zA-Z0-9_一-龥-]/g, '_').replace(/__+/g, '_')
      : 'bundle';
    const def = `${safeName}-${stamp}.nxbundle`;
    setFilename((cur) => {
      // Only overwrite if empty, or if it still looks like a previous default
      // (matches `<anything>-YYYYMMDD.nxbundle`).
      if (!cur) return def;
      if (/-\d{8}\.nxbundle$/.test(cur)) return def;
      return cur;
    });
  }, [selectedTeam, teams]);

  // When agent selection changes, prefetch skills + social entities + files lazily
  useEffect(() => {
    selectedAgents.forEach((aid) => {
      if (!skillsForAgents[aid]) {
        api.listSkills(aid, true)
          .then((r) => {
            const list: SkillEntry[] = (r.skills || []).map((sk: any) => {
              const path: string = sk.path || '';
              // Last path segment = filesystem-unique dir name within skills/
              const dirName = path.split('/').filter(Boolean).pop() || sk.name;
              return { name: sk.name, dirName, path };
            });
            setSkillsForAgents((s) => ({ ...s, [aid]: list }));
          }).catch(() => {});
      }
      if (!socialEntities[aid]) {
        api.getSocialNetworkList(aid)
          .then((r: any) => {
            // Verified shape: { success, entities: [...], count }
            // Each entity: entity_id, entity_name, entity_type, entity_description,
            //              tags, keywords, identity_info, contact_info, persona, ...
            const entities: SocialEntity[] = (r.entities || []).map((e: any) => ({
              entity_id: e.entity_id,
              entity_type: e.entity_type,
              entity_name: e.entity_name,
              entity_description: e.entity_description,
              tags: e.tags || e.keywords || [],
            }));
            setSocialEntities((s) => ({ ...s, [aid]: entities }));
          }).catch(() => {});
      }
      if (!workspaceFiles[aid]) {
        api.listFiles(aid)
          .then((r: any) => {
            // Verified shape: { success, files: [{filename, size, modified_at}],
            //                   workspace_path, error }
            // NB: only top-level files are listed (no subdir recursion).
            const files = (r.files || []).map((f: any) => ({
              path: f.filename,
              size: f.size || 0,
              sensitive: isSensitive(f.filename),
            }));
            setWorkspaceFiles((s) => ({ ...s, [aid]: files }));
          }).catch(() => {});
      }
      // Chat history (B2/P1) — actual API shape:
      //   { narratives: [{narrative_id, name, description, current_summary,
      //                   instances, ...}], events: [{event_id, narrative_id,
      //                   final_output, ...}] }   <- events are FLAT, not nested!
      // Plus we fetch jobs (/api/jobs?agent_id=...) and group by narrative_id (P7).
      if (!historyByAgent[aid]) {
        Promise.all([
          // Pass event_limit=0 so the backend doesn't cap at its default
          // 50 — users need to be able to cancel ANY event in a long
          // narrative (one of ours has 1351 events). The handful of MB
          // this brings into the page is fine; render-side pagination
          // below keeps the DOM small.
          api.getChatHistory(aid, 0).catch(() => null),
          api.getJobs(aid).catch(() => null),
        ]).then(([chRaw, jobsRaw]: any[]) => {
          const ch: any = chRaw || {};
          const jobs: any = jobsRaw || {};
          // Bucket events by narrative_id, then sort each bucket
          // newest → oldest. The bundle UI renders only the first
          // `displayLimit` items in each list (default 30, expandable),
          // so putting the most-recent events on top means "the chat
          // I was just having" is what shows up by default — which is
          // almost always what the user wants to inspect or strip.
          const eventsByNar: Record<string, ChatHistoryEvent[]> = {};
          for (const e of (ch.events || [])) {
            const nid = e.narrative_id;
            if (!nid) continue;
            (eventsByNar[nid] ??= []).push({
              event_id: e.event_id,
              trigger: e.trigger,
              created_at: e.created_at,
              preview: (e.final_output || '').slice(0, 100),
            });
          }
          for (const nid of Object.keys(eventsByNar)) {
            eventsByNar[nid].sort((a, b) =>
              (b.created_at || '').localeCompare(a.created_at || ''),
            );
          }
          // Bucket jobs by narrative_id (jobs without parent narrative go in
          // a synthetic "(no narrative)" bucket the user can still skip)
          const jobsByNar: Record<string, ChatHistoryJob[]> = {};
          for (const j of (jobs.jobs || [])) {
            const nid = j.narrative_id || '__orphan__';
            (jobsByNar[nid] ??= []).push({
              job_id: j.job_id,
              title: j.title,
              description: j.description,
              status: j.status,
              job_type: j.job_type,
            });
          }
          const narrs: ChatHistoryNarrative[] = (ch.narratives || []).map((n: any) => ({
            narrative_id: n.narrative_id,
            title: n.name || n.narrative_id,                 // human-readable
            description: n.description,
            current_summary: n.current_summary,
            type: n.type,
            instances_count: (n.instances || []).length,
            events: eventsByNar[n.narrative_id] || [],
            jobs: jobsByNar[n.narrative_id] || [],
            created_at: n.created_at,
          }));
          // Surface orphan jobs (no parent narrative) under a synthetic row
          if (jobsByNar['__orphan__']) {
            narrs.push({
              narrative_id: '__orphan_jobs__',
              title: '(jobs without a parent narrative)',
              events: [],
              jobs: jobsByNar['__orphan__'],
            });
          }
          setHistoryByAgent((s) => ({ ...s, [aid]: narrs }));
        });
      }
    });
  }, [selectedAgents, userId]);

  // Fetch the candidate Message Bus channel list whenever the closure
  // changes. Defaults to "select all" (matches legacy behavior); the user
  // can untick channels in the Bus tab. We track `busSelectionTouched` so
  // a closure refresh doesn't clobber a user's existing manual picks for
  // channels still in the new candidate set.
  useEffect(() => {
    const ids = Array.from(selectedAgents);
    if (ids.length === 0) {
      setBusChannels([]);
      setBusSelected(new Set());
      return;
    }
    setBusChannels(null);  // mark loading
    api.previewBusChannels(ids)
      .then((r) => {
        const candidates = r.channels || [];
        setBusChannels(candidates);
        const candidateIds = new Set(candidates.map((c) => c.channel_id));
        setBusSelected((cur) => {
          if (!busSelectionTouched) {
            return new Set(candidateIds);  // default = all
          }
          // Preserve manual picks that are still candidates; drop stale ones.
          const next = new Set<string>();
          cur.forEach((id) => { if (candidateIds.has(id)) next.add(id); });
          return next;
        });
      })
      .catch(() => {
        setBusChannels([]);
      });
  }, [selectedAgents]);

  // Full snapshot mode: Bus is treated like every other depth dial — auto-include
  // every candidate channel. The Bus tab becomes read-only; flipping back to
  // Custom restores user-controlled selection.
  useEffect(() => {
    if (mode !== 'full') return;
    if (!busChannels) return;
    setBusSelected(new Set(busChannels.map((c) => c.channel_id)));
    setBusSelectionTouched(false);
  }, [mode, busChannels]);

  // Fetch artifacts + MCPs per closure agent. Each agent is fetched once and
  // cached; agent-deselection doesn't evict (cheap memory, avoids re-fetch
  // when user toggles back). The "default = all artifacts selected" /
  // "default = no MCP selected" semantics apply when the data first lands.
  useEffect(() => {
    Array.from(selectedAgents).forEach((aid) => {
      if (artifactsForAgents[aid] === undefined) {
        // Mark pending so we don't double-fire on re-render
        setArtifactsForAgents((s) => ({ ...s, [aid]: null }));
        api.previewArtifacts([aid])
          .then((r) => {
            const list = r.agents?.[aid] || [];
            setArtifactsForAgents((s) => ({ ...s, [aid]: list }));
            // Default: select every artifact for this agent.
            setArtifactSelected((s) => {
              if (s[aid]) return s;  // user already touched
              return { ...s, [aid]: new Set(list.map((a) => a.artifact_id)) };
            });
          })
          .catch(() => setArtifactsForAgents((s) => ({ ...s, [aid]: [] })));
      }
      if (mcpsForAgents[aid] === undefined) {
        setMcpsForAgents((s) => ({ ...s, [aid]: null }));
        api.previewMcps([aid])
          .then((r) => {
            const list = r.agents?.[aid] || [];
            setMcpsForAgents((s) => ({ ...s, [aid]: list }));
            // Default: NO MCP selected — user opts in.
            setMcpSelected((s) => {
              if (s[aid]) return s;
              return { ...s, [aid]: new Set() };
            });
          })
          .catch(() => setMcpsForAgents((s) => ({ ...s, [aid]: [] })));
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedAgents]);

  // Full snapshot mode: opt-in to everything — every artifact AND every MCP
  // for selected agents. Custom mode keeps user picks.
  useEffect(() => {
    if (mode !== 'full') return;
    setArtifactSelected(() => {
      const out: Record<string, Set<string>> = {};
      Object.entries(artifactsForAgents).forEach(([aid, list]) => {
        if (list) out[aid] = new Set(list.map((a) => a.artifact_id));
      });
      return out;
    });
    setMcpSelected(() => {
      const out: Record<string, Set<string>> = {};
      Object.entries(mcpsForAgents).forEach(([aid, list]) => {
        if (list) out[aid] = new Set(list.map((m) => m.mcp_id));
      });
      return out;
    });
  }, [mode, artifactsForAgents, mcpsForAgents]);

  // Default skill choice per (agent, dirName) based on archive availability.
  // Note: skill_archives is keyed by skill_name (not dir), so two skills
  // with the same `name:` from frontmatter will share the same archive
  // lookup. That's fine for the URL/Zip default — the user can still
  // override per-row. dir_name is what disambiguates the actual dir.
  useEffect(() => {
    const newChoices: Record<string, SkillExportSpec> = {};
    Object.entries(skillsForAgents).forEach(([aid, list]) => {
      list.forEach(({ name, dirName }) => {
        const key = skillKey(aid, dirName);
        if (skillChoices[key]) {
          newChoices[key] = skillChoices[key];
          return;
        }
        const arch = skillArchives.find((a) => a.skill_name === name);
        const base = {
          skill_name: name,
          agent_id: aid,
          skill_dir: dirName,
        };
        if (arch?.source_url) {
          newChoices[key] = {
            ...base,
            install_method: 'url',
            source_url: arch.source_url,
            source_type: 'github',
            branch: 'main',
          };
        } else if (arch?.archive_path) {
          newChoices[key] = {
            ...base,
            install_method: 'zip',
            archive_path: arch.archive_path,
          };
        } else {
          newChoices[key] = { ...base, install_method: 'full_copy' };
        }
      });
    });
    setSkillChoices(newChoices);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(skillsForAgents), JSON.stringify(skillArchives)]);

  // P8: default social entity selection — only entries that reference
  // agents IN this bundle's closure. Strict matching:
  //   * entity_type MUST be 'agent' (skip user/organization/etc — user can
  //     manually opt them in)
  //   * AND one of:
  //       - entity_id ∈ closure (or contains a closure agent_id substring)
  //       - entity_name == any closure agent's name (case-insensitive
  //         exact-or-token match — not naive substring like "Loki" matching
  //         "lokitrickster")
  //       - any closure agent's name OR agent_id appears as a TOKEN in
  //         tags / description / aliases / contact_info / extra_data
  // We avoid pure substring search because real entities like "Loki" /
  // "Iris" are 3-4 letters and would over-match (e.g. an entity named
  // "iris_8ec0" would match "Iris", a teammate name, even though they're
  // different agents). Word-boundary regex avoids that.
  useEffect(() => {
    // Full mode = "ship everything"; the dedicated Full-mode effect below
    // handles social-entity selection (selects every entity), so this
    // strict-matching default-selector should NOT run in Full mode (it
    // would otherwise overwrite the all-selected state on every render).
    if (mode === 'full') return;
    // Build the closure-agent identity set: each entry has both name and id
    // we should match on. Names can repeat (different agent same name) so we
    // dedupe per closure agent.
    const closureAgents: { id: string; nameLower: string; idLower: string }[] = [];
    Array.from(selectedAgents).forEach((aid) => {
      const a = agents.find((x) => x.agent_id === aid);
      const nm = (a?.name || '').trim();
      if (nm) {
        closureAgents.push({ id: aid, nameLower: nm.toLowerCase(), idLower: aid.toLowerCase() });
      } else {
        closureAgents.push({ id: aid, nameLower: '', idLower: aid.toLowerCase() });
      }
    });

    const wordBoundary = (haystack: string, needle: string): boolean => {
      if (!needle) return false;
      const escaped = needle.replace(/[-/\\^$*+?.()|[\]{}]/g, '\\$&');
      // Treat any non-alnum-Chinese as boundary
      const re = new RegExp(`(^|[^\\w\\u4e00-\\u9fa5])${escaped}([^\\w\\u4e00-\\u9fa5]|$)`, 'i');
      return re.test(haystack);
    };

    const matchesClosure = (e: SocialEntity): boolean => {
      // Only agent-typed entities are eligible for default selection (P8).
      if (e.entity_type !== 'agent') return false;
      const eid = (e.entity_id || '').toLowerCase();
      const en = (e.entity_name || '').toLowerCase();
      // Aggregate every text field into one haystack we can scan
      const haystackParts: string[] = [
        eid, en,
        (e.entity_description || ''),
        ...(e.tags || []),
      ];
      const hay = haystackParts.filter(Boolean).join('\n').toLowerCase();
      for (const ca of closureAgents) {
        // Exact id match
        if (eid === ca.idLower) return true;
        // Name match (case-insensitive exact, or token-bounded inside a longer string)
        if (ca.nameLower && (en === ca.nameLower || wordBoundary(en, ca.nameLower))) return true;
        // Any closure agent's full id or name appears as token in haystack
        if (ca.nameLower && wordBoundary(hay, ca.nameLower)) return true;
        if (wordBoundary(hay, ca.idLower)) return true;
      }
      return false;
    };

    const next: Record<string, Set<string>> = {};
    Object.entries(socialEntities).forEach(([aid, list]) => {
      const set = new Set<string>();
      list.forEach((e) => {
        if (matchesClosure(e)) set.add(e.entity_id);
      });
      next[aid] = set;
    });
    setSocialSelected(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, JSON.stringify(socialEntities), JSON.stringify(Array.from(selectedAgents)), JSON.stringify(agents.map(a => a.agent_id + ':' + a.name))]);

  function isSensitive(p: string): boolean {
    const lc = p.toLowerCase();
    return /\.env(\b|\.)|\.aws\/|\.ssh\/|\.git\/config|id_rsa|\.pem|\.key|credentials\.|_token|_secret/.test(lc);
  }

  function toggleAgent(aid: string) {
    // Full mode still allows agent selection — see PRD note at the top of the
    // Full-mode pre-fill effect. Only the *granularity* tabs (skills/social/
    // workspace/history) become read-only.
    setSelectedAgents((prev) => {
      const next = new Set(prev);
      if (next.has(aid)) next.delete(aid);
      else next.add(aid);
      return next;
    });
  }

  function toggleSocial(aid: string, eid: string) {
    if (mode === 'full') return;
    setSocialSelected((prev) => {
      const next = { ...prev };
      const set = new Set(next[aid] || []);
      if (set.has(eid)) set.delete(eid); else set.add(eid);
      next[aid] = set;
      return next;
    });
  }

  async function toggleWorkspaceFile(aid: string, path: string) {
    if (mode === 'full') return;
    const file = (workspaceFiles[aid] || []).find((f) => f.path === path);
    const isSensitive = !!file?.sensitive;
    const inSet = (workspaceExcludes[aid] || new Set()).has(path);

    // workspaceExcludes set has DUAL meaning per file kind:
    //   - non-sensitive file: presence ↔ user-excluded (default include)
    //   - sensitive file:     presence ↔ user override-include (default exclude)
    //
    // So clicking the checkbox does:
    //   - non-sensitive, NOT in set → add to set       : default-include → exclude   (no prompt)
    //   - non-sensitive, in set     → remove from set  : excluded → default-include  (no prompt)
    //   - sensitive,     NOT in set → add to set       : default-skip → user opts-in (CONFIRM)
    //   - sensitive,     in set     → remove from set  : user-override-include → back to default skip (no prompt)
    //
    // Confirm modal only fires when the click is going to put a sensitive
    // file's bytes INTO the bundle for the first time.
    const isAddingToSet = !inSet;
    const isOptingInSensitive = isSensitive && isAddingToSet;

    if (isOptingInSensitive) {
      const ok = await confirmDialog({
        title: 'Include sensitive file?',
        message: (
          <>
            <p><code>{path}</code> matches a sensitive-pattern (e.g. <code>.env</code>, <code>*.key</code>, credentials, wallet).</p>
            <p>Including it will ship the file's contents inside the bundle. Anyone you share the bundle with will receive these bytes.</p>
            <p>Confirm only if you've verified this file does not contain secrets you want to keep private.</p>
          </>
        ),
        confirmText: 'Include anyway',
        danger: true,
      });
      if (!ok) return;
    }
    setWorkspaceExcludes((prev) => {
      const next = { ...prev };
      const set = new Set(next[aid] || []);
      if (set.has(path)) set.delete(path); else set.add(path);
      next[aid] = set;
      return next;
    });
  }

  const summary = useMemo(() => {
    return {
      agents: selectedAgents.size,
      skills: Object.values(skillChoices).filter((s) => s && s.install_method).length,
      socialEntities: Object.values(socialSelected).reduce((a, b) => a + b.size, 0),
      workspaceExcluded: Object.values(workspaceExcludes).reduce((a, b) => a + b.size, 0),
      busChannels: busSelected.size,
      artifacts: Object.values(artifactSelected).reduce((a, b) => a + b.size, 0),
      mcps: Object.values(mcpSelected).reduce((a, b) => a + b.size, 0),
    };
  }, [selectedAgents, skillChoices, socialSelected, workspaceExcludes, busSelected, artifactSelected, mcpSelected]);

  // Full mode: pre-fill granularity to "everything for the selected agents",
  // but DO NOT touch the agent selection itself — the user picks which agents
  // to ship (you might want to copy 3 of 11 agents to a new machine, not all
  // 11). PRD §5 议题 2: Full vs Custom is depth (with-credentials vs
  // strip-credentials), not breadth.
  useEffect(() => {
    if (mode !== 'full') return;
    // 2. Force every (agent, dir) pair to full_copy mode.
    const next: Record<string, SkillExportSpec> = { ...skillChoices };
    Object.entries(skillsForAgents).forEach(([aid, list]) => {
      list.forEach(({ name, dirName }) => {
        next[skillKey(aid, dirName)] = {
          skill_name: name,
          agent_id: aid,
          skill_dir: dirName,
          install_method: 'full_copy',
        };
      });
    });
    setSkillChoices(next);
    // 3. Select EVERY social entity for every agent (no type filter — Full
    //    ships agent / user / organization / etc.).
    setSocialSelected((cur) => {
      const out = { ...cur };
      Object.entries(socialEntities).forEach(([aid, list]) => {
        out[aid] = new Set(list.map((e) => e.entity_id));
      });
      return out;
    });
    // 4. History: include all narratives + events + jobs.
    //    Events use opt-in semantics now (default empty), so for the
    //    Full preset we pre-fill includedEvents with EVERY event_id
    //    per narrative across all loaded agents. excludedNarratives
    //    stays empty (exclusion semantics; empty = all included).
    setExcludedNarratives({});
    setIncludedEvents(() => {
      const out: Record<string, Set<string>> = {};
      Object.values(historyByAgent).forEach((narrs) => {
        narrs.forEach((n) => {
          if (n.narrative_id === '__orphan_jobs__') return;
          if (n.events.length > 0) {
            out[n.narrative_id] = new Set(n.events.map((e) => e.event_id));
          }
        });
      });
      return out;
    });
    setExcludedJobs({});
    setIncludeChat(true);
    // 5. Workspace excludes: dual-meaning set (see toggleWorkspaceFile).
    //    Non-sensitive default = include (set membership = exclude). Full
    //    keeps non-sensitive set entries empty.
    //    Sensitive default = exclude (set membership = override-include).
    //    For Full snapshot we want sensitive files INCLUDED, so we PRELOAD
    //    every sensitive file path into the override-include set per agent.
    setWorkspaceExcludes(() => {
      const out: Record<string, Set<string>> = {};
      Object.entries(workspaceFiles).forEach(([aid, files]) => {
        const sensitivePaths = files.filter((f) => f.sensitive).map((f) => f.path);
        if (sensitivePaths.length > 0) out[aid] = new Set(sensitivePaths);
      });
      return out;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, JSON.stringify(skillsForAgents), JSON.stringify(socialEntities), JSON.stringify(workspaceFiles)]);

  async function doExport() {
    setDownloading(true);
    try {
      const skills: SkillExportSpec[] = Object.values(skillChoices).filter((s) => !!s.install_method);
      const social: Record<string, string[]> = {};
      Object.entries(socialSelected).forEach(([aid, set]) => {
        social[aid] = Array.from(set);
      });
      // P6: workspaceExcludes set has dual meaning per file:
      //   - non-sensitive file in set → user wants to EXCLUDE it
      //   - sensitive file in set → user opted-IN (override the default-skip)
      // Final exclude list emitted to backend = (non-sensitive in set) ∪
      // (sensitive NOT in set). This way sensitive files default-skip but the
      // user can opt-in via the confirm modal.
      const excludes: Record<string, string[]> = {};
      Array.from(selectedAgents).forEach((aid) => {
        const allFiles = workspaceFiles[aid] || [];
        const userSet = workspaceExcludes[aid] || new Set();
        const out = new Set<string>();
        for (const f of allFiles) {
          if (f.sensitive) {
            // default: exclude. user-override-set membership flips to include.
            if (!userSet.has(f.path)) out.add(f.path);
          } else {
            // default: include. set membership = exclude.
            if (userSet.has(f.path)) out.add(f.path);
          }
        }
        if (out.size > 0) excludes[aid] = Array.from(out);
      });
      // Derive narrative + event allowlists for the backend payload.
      //
      // narratives — "exclusion set" semantics:
      //   default = ship all, user-unchecked narratives land in
      //   excludedNarratives[aid]. Only emit narrativeSel[aid] when
      //   the user actually de-selected something, else leave
      //   undefined (= backend ships all).
      //
      // events — "inclusion set" semantics (2026-05-18, opt-in
      // default):
      //   default = ship none. User-checked event_ids land in
      //   includedEvents[nid]. We always emit eventSel as an explicit
      //   object (possibly empty) so the backend's `is not None`
      //   check locks the bundle into opt-in mode. An empty object
      //   means "ship 0 events for every narrative shipped"; missing
      //   narrative keys → backend's `.get(nid, [])` ships 0 for them
      //   too. Adding an entry `nid: [event_ids]` ships those.
      const narrativeSel: Record<string, string[]> = {};
      const eventSel: Record<string, string[]> = {};
      Array.from(selectedAgents).forEach((aid) => {
        const allNarrs = historyByAgent[aid] || [];
        // Filter out the synthetic orphan-jobs placeholder narrative —
        // it's a UI-only construct, not a real narrative_id, and
        // shouldn't enter the backend allowlist.
        const realNarrs = allNarrs.filter((n) => n.narrative_id !== '__orphan_jobs__');
        const exNars = excludedNarratives[aid] || new Set();
        if (exNars.size > 0) {
          narrativeSel[aid] = realNarrs
            .filter((n) => !exNars.has(n.narrative_id))
            .map((n) => n.narrative_id);
        }
        realNarrs.forEach((n) => {
          const inEvts = includedEvents[n.narrative_id];
          if (inEvts && inEvts.size > 0) {
            eventSel[n.narrative_id] = n.events
              .filter((e) => inEvts.has(e.event_id))
              .map((e) => e.event_id);
          }
        });
      });
      // P7: per-agent job allowlist derived from `excludedJobs`. We only
      // emit a selection if the user actually de-selected something (else
      // backend's "include all" default applies).
      const jobSel: Record<string, string[]> = {};
      Array.from(selectedAgents).forEach((aid) => {
        const allNarrs = historyByAgent[aid] || [];
        const exNars = excludedNarratives[aid] || new Set();
        const ids: string[] = [];
        let hasExclusion = false;
        allNarrs.forEach((n) => {
          if (exNars.has(n.narrative_id)) return;  // narrative excluded → builder drops jobs anyway
          const exJobs = excludedJobs[n.narrative_id];
          if (exJobs && exJobs.size > 0) {
            hasExclusion = true;
            n.jobs.forEach((j) => { if (!exJobs.has(j.job_id)) ids.push(j.job_id); });
          } else {
            n.jobs.forEach((j) => ids.push(j.job_id));
          }
        });
        if (hasExclusion) jobSel[aid] = ids;
      });

      const payload: BundleExportRequest = {
        agent_ids: Array.from(selectedAgents),
        team_id: selectedTeam || null,
        team_intro_md: introMd || null,
        skills,
        social_entity_selection: social,
        workspace_excludes: excludes,
        include_chat_history: includeChat,
        // Full mode user has already accepted "ship credentials" semantics by
        // picking that mode; auto-flag so they don't have to confirm twice.
        accept_sensitive_zips: mode === 'full' ? true : acceptSensitiveZips,
        narrative_selection: Object.keys(narrativeSel).length ? narrativeSel : null,
        // Always emit eventSel as an explicit object (even when empty)
        // so the backend's `is not None` check locks the bundle into
        // opt-in mode — empty dict means "ship 0 events", which is
        // what the user gets when they haven't checked any event
        // checkboxes. Sending null here would fall back to legacy
        // "ship all events" semantics, which is the bug Bin哥 caught.
        event_selection: includeChat ? eventSel : null,
        job_selection: Object.keys(jobSel).length ? jobSel : null,
        // Bus channel allowlist. Only emit when the user actually deselected
        // something (else the backend's default "ship every owner-owned channel
        // with ≥1 closure member" applies, and we don't want a stale list to
        // override that). We compare to the candidate list at the time of
        // export — Full mode auto-fills back to all-candidates, so we'll skip
        // the field there too.
        bus_channel_selection: (busChannels && busChannels.length > 0
          && busSelected.size !== busChannels.length)
          ? Array.from(busSelected)
          : null,
        // MCP: opt-in semantics. We always emit an explicit object (even if
        // empty) so the backend's "None / {} = ship nothing" branch reliably
        // triggers regardless of frontend version. Only include agents that
        // actually have at least one MCP picked.
        mcp_selection: (() => {
          const out: Record<string, string[]> = {};
          Object.entries(mcpSelected).forEach(([aid, set]) => {
            if (set.size > 0) out[aid] = Array.from(set);
          });
          return Object.keys(out).length ? out : null;
        })(),
        // Artifacts: default-include semantics. Only emit selection when the
        // user actually unchecked something for an agent (a.k.a. selection
        // size < known list size); otherwise fall back to "include all".
        artifact_selection: (() => {
          const out: Record<string, string[]> = {};
          Array.from(selectedAgents).forEach((aid) => {
            const known = artifactsForAgents[aid];
            const picked = artifactSelected[aid];
            if (!known || !picked) return;
            if (picked.size === known.length) return;  // include-all default
            out[aid] = Array.from(picked);
          });
          return Object.keys(out).length ? out : null;
        })(),
      };
      const { blob, filename: serverFilename, warningsCount, externalEdgesDropped } = await api.exportBundle(payload);
      // (filename sanitization happens just below; reuse `finalName`)
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      // Warnings are real concerns; "external edges dropped" is informational
      // (expected closure behavior) and shown separately so the user doesn't
      // panic over hundreds of routine edge-drops.
      // Sanitize filename: strip path-traversal chars, ensure .nxbundle suffix.
      let chosen = (filename && filename.trim()) || serverFilename;
      // Disallow `/` and `\` to avoid the browser writing into a subdirectory.
      chosen = chosen.replace(/[\/\\]/g, '_');
      if (!chosen.endsWith('.nxbundle') && !chosen.endsWith('.zip')) {
        chosen = `${chosen}.nxbundle`;
      }
      const finalName = chosen;
      a.download = finalName;
      a.click();
      URL.revokeObjectURL(url);
      const parts = [`${finalName} downloaded.`];
      if (externalEdgesDropped > 0) {
        parts.push(
          `Dropped ${externalEdgesDropped} reference(s) to entities outside the bundle ` +
          '(expected — your agents had social-network notes about agents not in the closure).'
        );
      }
      if (warningsCount > 0) {
        parts.push(`${warningsCount} warning${warningsCount === 1 ? '' : 's'} (see manifest.json).`);
      }
      await alert({
        title: 'Bundle created',
        message: parts.join(' '),
      });
      navigate('/app/settings');
    } catch (e: any) {
      console.error(e);
      // B6: detect 409 SENSITIVE_FILES_IN_SKILL_ZIP and surface confirmation modal
      if (e?.code === 'SENSITIVE_FILES_IN_SKILL_ZIP' && e?.hits) {
        setSensitiveHits(e.hits);
      } else {
        await alert({ title: 'Export failed', message: e?.message || String(e), danger: true });
      }
    } finally {
      setDownloading(false);
      setReviewing(false);
    }
  }

  return (
    <div className="h-full flex flex-col" style={{ background: 'var(--nm-card)' }}>
      {/* Header — NM display title + bracket-section count line */}
      <div
        className="px-6 py-4 border-b flex items-center justify-between gap-3"
        style={{ borderColor: 'var(--nm-hairline)' }}
      >
        <div className="flex items-center gap-3 min-w-0">
          <button
            onClick={() => navigate('/app/settings')}
            className="p-1 rounded-[var(--radius-xs)] transition-colors hover:bg-[color:var(--nm-paper-warm)]"
            aria-label="Back to settings"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <Package className="w-5 h-5" style={{ color: 'var(--nm-ink50)' }} />
          <h1
            className="text-2xl font-bold tracking-tight"
            style={{ color: 'var(--nm-ink)', fontFamily: 'var(--font-display)' }}
          >
            Export bundle
          </h1>
        </div>
        <BracketSectionLabel>
          {summary.agents} agents · {summary.skills} skills · {summary.socialEntities} entities · {summary.busChannels} channels
        </BracketSectionLabel>
      </div>

      {/* Mode picker (PRD §5 议题 2) */}
      <div className="px-6 py-3 border-b border-[var(--border-subtle)] bg-[var(--bg-secondary)]">
        <div className="flex items-start gap-3">
          <span className="text-[10px] uppercase tracking-widest text-[var(--text-tertiary)] mt-1.5 font-mono shrink-0">
            Mode
          </span>
          <div className="flex-1 grid grid-cols-2 gap-2">
            <button
              onClick={() => setMode('full')}
              className={cn(
                'text-left p-3 border transition-colors',
                mode === 'full'
                  ? 'border-[var(--text-primary)] bg-[var(--bg-elevated)]'
                  : 'border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]'
              )}
            >
              <div className="flex items-center gap-2">
                <span className={cn(
                  'w-3 h-3 rounded-full border-2',
                  mode === 'full' ? 'border-[var(--text-primary)] bg-[var(--text-primary)]' : 'border-[var(--text-tertiary)]'
                )} />
                <span className="font-mono text-sm">Full snapshot</span>
                <span className="text-[10px] px-1.5 py-0.5 border border-[var(--color-yellow-500)] text-[var(--color-yellow-500)]">
                  contains_secrets
                </span>
              </div>
              <div className="text-[11px] text-[var(--text-tertiary)] mt-1.5 leading-relaxed">
                Self-backup. Includes <strong>all</strong> narratives, events, jobs,
                social entities, message-bus channels, workspace files, and skills
                with credentials (env_config, wallet.json, study summaries). Use to
                clone an entire setup to another of YOUR machines.
              </div>
            </button>
            <button
              onClick={() => setMode('custom')}
              className={cn(
                'text-left p-3 border transition-colors',
                mode === 'custom'
                  ? 'border-[var(--text-primary)] bg-[var(--bg-elevated)]'
                  : 'border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]'
              )}
            >
              <div className="flex items-center gap-2">
                <span className={cn(
                  'w-3 h-3 rounded-full border-2',
                  mode === 'custom' ? 'border-[var(--text-primary)] bg-[var(--text-primary)]' : 'border-[var(--text-tertiary)]'
                )} />
                <span className="font-mono text-sm">Custom (recommended for sharing)</span>
              </div>
              <div className="text-[11px] text-[var(--text-tertiary)] mt-1.5 leading-relaxed">
                Pick exactly which agents, narratives, events, social entities,
                workspace files and skills go in. Skill credentials are <strong>stripped</strong>
                {' '}(unless you explicitly choose Full Copy per skill). Use to share with someone else.
              </div>
            </button>
          </div>
        </div>
        {mode === 'full' && (
          <div className="mt-2 ml-[60px] text-[11px] text-[var(--color-yellow-500)] flex items-center gap-1.5">
            <AlertTriangle className="w-3 h-3" />
            Pick which agents to ship in the Agents tab. For each picked agent,
            ALL their skills become Full Copy (carry credentials), narratives /
            events / social entities / workspace files are fully included.
            Below tabs are read-only previews.
          </div>
        )}
      </div>

      {/* Tab bar */}
      <div className="px-6 border-b border-[var(--border-subtle)] flex">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={cn(
                'px-4 py-3 text-sm font-mono flex items-center gap-2 border-b-2 -mb-px',
                tab === t.id
                  ? 'border-[var(--text-primary)] text-[var(--text-primary)]'
                  : 'border-transparent text-[var(--text-tertiary)] hover:text-[var(--text-primary)]'
              )}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-6">
        {tab === 'agents' && (
          <AgentsTab
            agents={agents}
            teams={teams}
            selected={selectedAgents}
            onToggle={toggleAgent}
            selectedTeam={selectedTeam}
            onSetTeam={setSelectedTeam}
            onBulkSet={setSelectedAgents}
          />
        )}
        {tab === 'history' && (
          <HistoryTab
            agents={agents.filter((a) => selectedAgents.has(a.agent_id))}
            historyByAgent={historyByAgent}
            excludedNarratives={excludedNarratives}
            includedEvents={includedEvents}
            excludedJobs={excludedJobs}
            onToggleNarrative={(aid, nid) => setExcludedNarratives((s) => {
              const next = { ...s };
              const cur = new Set(next[aid] || []);
              if (cur.has(nid)) cur.delete(nid); else cur.add(nid);
              next[aid] = cur;
              return next;
            })}
            onToggleEvent={(nid, eid) => setIncludedEvents((s) => {
              const next = { ...s };
              const cur = new Set(next[nid] || []);
              if (cur.has(eid)) cur.delete(eid); else cur.add(eid);
              next[nid] = cur;
              return next;
            })}
            onToggleJob={(nid, jid) => setExcludedJobs((s) => {
              const next = { ...s };
              const cur = new Set(next[nid] || []);
              if (cur.has(jid)) cur.delete(jid); else cur.add(jid);
              next[nid] = cur;
              return next;
            })}
            onSelectAllNarratives={(aid) => setExcludedNarratives((s) => ({
              ...s, [aid]: new Set(),
            }))}
            onSelectNoneNarratives={(aid) => setExcludedNarratives((s) => ({
              ...s, [aid]: new Set((historyByAgent[aid] || []).map((n) => n.narrative_id)),
            }))}
            onSelectAllEventsInNarrative={(nid, allIds) => setIncludedEvents((s) => ({
              ...s, [nid]: new Set(allIds),
            }))}
            onSelectNoneEventsInNarrative={(nid) => setIncludedEvents((s) => ({
              ...s, [nid]: new Set(),
            }))}
            onSelectAllJobsInNarrative={(nid) => setExcludedJobs((s) => ({
              ...s, [nid]: new Set(),
            }))}
            onSelectNoneJobsInNarrative={(nid, allIds) => setExcludedJobs((s) => ({
              ...s, [nid]: new Set(allIds),
            }))}
            eventDisplayLimit={eventDisplayLimit}
            eventPageSize={EVENT_PAGE_SIZE}
            onShowMoreEvents={(nid, total) => setEventDisplayLimit((s) => ({
              ...s,
              [nid]: Math.min(
                (s[nid] ?? EVENT_PAGE_SIZE) + EVENT_PAGE_SIZE,
                total,
              ),
            }))}
            onShowAllEvents={(nid, total) => setEventDisplayLimit((s) => ({
              ...s, [nid]: total,
            }))}
            chatHistoryEnabled={includeChat}
          />
        )}
        {tab === 'skills' && (
          <div className="flex flex-col">
            <SkillsTab
              agents={agents.filter((a) => selectedAgents.has(a.agent_id))}
              userId={userId}
              skillsForAgents={skillsForAgents}
              skillArchives={skillArchives}
              skillChoices={skillChoices}
              mode={mode}
              onChange={(agentId, dirName, spec) =>
                setSkillChoices((s) => ({ ...s, [skillKey(agentId, dirName)]: { ...spec, agent_id: agentId, skill_dir: dirName } }))
              }
              onAfterBackup={() => {
                api.listSkillArchives().then((r) => setSkillArchives(r.archives)).catch(() => {});
              }}
            />
            <McpSection
              agents={agents.filter((a) => selectedAgents.has(a.agent_id))}
              mcpsByAgent={mcpsForAgents}
              selectedByAgent={mcpSelected}
              mode={mode}
              onToggle={(aid, mid) => {
                setMcpSelected((cur) => {
                  const next = { ...cur };
                  const set = new Set(next[aid] || []);
                  if (set.has(mid)) set.delete(mid); else set.add(mid);
                  next[aid] = set;
                  return next;
                });
              }}
              onSelectAllForAgent={(aid) => {
                setMcpSelected((cur) => {
                  const list = mcpsForAgents[aid] || [];
                  return { ...cur, [aid]: new Set(list.map((m) => m.mcp_id)) };
                });
              }}
              onClearForAgent={(aid) => {
                setMcpSelected((cur) => ({ ...cur, [aid]: new Set() }));
              }}
            />
          </div>
        )}
        {tab === 'social' && (
          <SocialTab
            entitiesByAgent={socialEntities}
            selectedByAgent={socialSelected}
            pageByAgent={socialPage}
            onTogglePage={(aid, p) => setSocialPage((s) => ({ ...s, [aid]: p }))}
            selectedPageByAgent={socialSelectedPage}
            onToggleSelectedPage={(aid, p) => setSocialSelectedPage((s) => ({ ...s, [aid]: p }))}
            agents={agents.filter((a) => selectedAgents.has(a.agent_id))}
            onToggle={toggleSocial}
            onBulkSet={(aid, ids) => setSocialSelected((s) => ({ ...s, [aid]: new Set(ids) }))}
            selectedTeam={selectedTeam}
            teams={teams}
          />
        )}
        {tab === 'bus' && (
          <BusTab
            agents={agents.filter((a) => selectedAgents.has(a.agent_id))}
            channels={busChannels}
            selected={busSelected}
            mode={mode}
            onToggle={(cid) => {
              setBusSelectionTouched(true);
              setBusSelected((cur) => {
                const next = new Set(cur);
                if (next.has(cid)) next.delete(cid); else next.add(cid);
                return next;
              });
            }}
            onSelectAll={() => {
              setBusSelectionTouched(true);
              setBusSelected(new Set((busChannels || []).map((c) => c.channel_id)));
            }}
            onSelectNone={() => {
              setBusSelectionTouched(true);
              setBusSelected(new Set());
            }}
          />
        )}
        {tab === 'artifacts' && (
          <ArtifactsTab
            agents={agents.filter((a) => selectedAgents.has(a.agent_id))}
            artifactsByAgent={artifactsForAgents}
            selectedByAgent={artifactSelected}
            mode={mode}
            onToggle={(aid, artId) => {
              setArtifactSelected((cur) => {
                const next = { ...cur };
                const set = new Set(next[aid] || []);
                if (set.has(artId)) set.delete(artId); else set.add(artId);
                next[aid] = set;
                return next;
              });
            }}
            onSelectAllForAgent={(aid) => {
              setArtifactSelected((cur) => {
                const list = artifactsForAgents[aid] || [];
                return { ...cur, [aid]: new Set(list.map((a) => a.artifact_id)) };
              });
            }}
            onClearForAgent={(aid) => {
              setArtifactSelected((cur) => ({ ...cur, [aid]: new Set() }));
            }}
          />
        )}
        {tab === 'workspace' && (
          <WorkspaceTab
            filesByAgent={workspaceFiles}
            excludesByAgent={workspaceExcludes}
            agents={agents.filter((a) => selectedAgents.has(a.agent_id))}
            onToggle={toggleWorkspaceFile}
            onBulkSet={(aid, scope) => {
              const files = workspaceFiles[aid] || [];
              setWorkspaceExcludes((s) => {
                const next = { ...s };
                if (scope === 'all') {
                  // all included: sensitive get override-include (in set);
                  // non-sensitive default-include (NOT in set)
                  next[aid] = new Set(files.filter((f) => f.sensitive).map((f) => f.path));
                } else if (scope === 'non-sensitive') {
                  // back to defaults: all non-sensitive included, all sensitive skipped
                  next[aid] = new Set();
                } else {
                  // exclude-all: every non-sensitive in set; no sensitive override
                  next[aid] = new Set(files.filter((f) => !f.sensitive).map((f) => f.path));
                }
                return next;
              });
            }}
          />
        )}
      </div>

      {/* Bundle notes (README.md) + filename (P9) */}
      <div className="px-6 py-4 border-t border-[var(--border-subtle)] bg-[var(--bg-secondary)]">
        {/* Filename input */}
        <div className="flex items-center gap-2 mb-3">
          <span className="text-xs font-mono uppercase tracking-widest text-[var(--text-tertiary)] shrink-0">
            File name
          </span>
          <input
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
            placeholder="bundle.nxbundle"
            className="flex-1 px-3 py-1.5 text-sm font-mono bg-[var(--bg-tertiary)] border border-[var(--border-default)] focus:outline-none"
          />
        </div>
        <div className="flex items-center gap-2 mb-2">
          <FileText className="w-4 h-4" />
          <span className="text-sm font-mono">Bundle Notes (optional, shown to recipient)</span>
        </div>
        <textarea
          value={introMd}
          onChange={(e) => setIntroMd(e.target.value)}
          rows={4}
          placeholder={`# ${selectedTeam ? teams.find((x) => x.team.team_id === selectedTeam)?.team.name : 'My team'}\n\nDescribe what this team does…`}
          className="w-full px-3 py-2 text-sm font-mono bg-[var(--bg-tertiary)] border border-[var(--border-default)] focus:outline-none resize-y"
        />
        <label className="mt-3 inline-flex items-center gap-2 text-xs text-[var(--text-secondary)]">
          <input
            type="checkbox"
            checked={includeChat}
            onChange={(e) => setIncludeChat(e.target.checked)}
          />
          Include chat history (events + messages). Disable for templates with privacy concerns.
        </label>
      </div>

      {/* Footer */}
      <div className="px-6 py-3 border-t border-[var(--border-default)] flex items-center justify-between">
        <Button onClick={() => navigate('/app/settings')} variant="ghost" size="sm">Cancel</Button>
        <Button
          onClick={() => setReviewing(true)}
          disabled={selectedAgents.size === 0}
          size="sm"
          className="gap-1"
        >
          <Search className="w-3.5 h-3.5" />
          Review &amp; Export
        </Button>
      </div>

      {reviewing && (
        <ReviewSummaryModal
          summary={summary}
          agents={Array.from(selectedAgents)}
          team={teams.find((t) => t.team.team_id === selectedTeam) || null}
          introMd={introMd}
          skills={Object.values(skillChoices)}
          warnings={collectWarnings(skillChoices, workspaceFiles, selectedAgents)}
          onCancel={() => setReviewing(false)}
          onConfirm={doExport}
          downloading={downloading}
          filename={filename}
          mode={mode}
        />
      )}
      {sensitiveHits && (
        <SensitiveZipConfirmModal
          hits={sensitiveHits}
          onCancel={() => setSensitiveHits(null)}
          onAccept={async () => {
            setAcceptSensitiveZips(true);
            setSensitiveHits(null);
            // Re-trigger export with the flag set
            setTimeout(() => doExport(), 50);
          }}
        />
      )}
      {dialog}
    </div>
  );
}

function collectWarnings(
  skills: Record<string, SkillExportSpec>,
  workspaceFiles: Record<string, any>,
  selectedAgents: Set<string>,
): string[] {
  const warns: string[] = [];
  Object.values(skills).forEach((s) => {
    if (s.install_method === 'full_copy') {
      warns.push(`"${s.skill_name}" full-copy mode — includes credentials/wallet/etc. if present`);
    }
  });
  selectedAgents.forEach((aid) => {
    const sens = (workspaceFiles[aid] || []).filter((f: any) => f.sensitive).length;
    if (sens > 0) warns.push(`agent ${aid}: ${sens} workspace file(s) match sensitive patterns (auto-excluded by default)`);
  });
  warns.push('Free text (awareness / events / messages) is NOT auto-scanned for inline secrets — verify yourself.');
  return warns;
}

// =============================================================================
// Sub-components
// =============================================================================

function AgentsTab({
  agents, teams, selected, onToggle, selectedTeam, onSetTeam, onBulkSet,
}: {
  agents: any[]; teams: TeamWithMembers[]; selected: Set<string>; onToggle: (id: string) => void;
  selectedTeam: string; onSetTeam: (t: string) => void;
  onBulkSet: (next: Set<string>) => void;
}) {
  // Pre-compute (team_id → existing-on-this-instance member ids) so that
  // batch select doesn't try to add agent_ids that no longer exist locally.
  const liveAgentIds = useMemo(() => new Set(agents.map((a) => a.agent_id)), [agents]);
  function teamLiveMembers(t: TeamWithMembers): string[] {
    return t.member_agent_ids.filter((id) => liveAgentIds.has(id));
  }
  function addTeam(t: TeamWithMembers) {
    const next = new Set(selected);
    teamLiveMembers(t).forEach((id) => next.add(id));
    onBulkSet(next);
  }
  function replaceWithTeam(t: TeamWithMembers) {
    onBulkSet(new Set(teamLiveMembers(t)));
  }
  function dropTeam(t: TeamWithMembers) {
    const next = new Set(selected);
    teamLiveMembers(t).forEach((id) => next.delete(id));
    onBulkSet(next);
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2 text-xs text-[var(--text-tertiary)] leading-relaxed border-l-2 border-[var(--accent-primary)]/40 pl-3">
        <p>
          <strong className="text-[var(--text-secondary)]">What you pick here:</strong> the
          set of agents this bundle will contain. Everything else in the wizard (narratives,
          skills, MCPs, files…) is filtered to this closure.
        </p>
        <p>
          Picking a team adds all of its existing members in one click; you can still
          uncheck individuals afterwards. External references (e.g. a social entity pointing
          at an agent outside this closure) get dropped automatically on export.
        </p>
      </div>
      <div>
        <label className="text-xs uppercase text-[var(--text-tertiary)]">Bundle this team (optional)</label>
        <select
          value={selectedTeam}
          onChange={(e) => onSetTeam(e.target.value)}
          className="mt-1 px-3 py-2 text-sm font-mono bg-[var(--bg-tertiary)] border border-[var(--border-default)]"
        >
          <option value="">— No team / individual agents —</option>
          {teams.map((t) => (
            <option key={t.team.team_id} value={t.team.team_id}>{t.team.name} ({t.member_agent_ids.length})</option>
          ))}
        </select>
      </div>

      {/* Per-team batch select — same gesture as the sidebar Package button:
          one click pulls in (or replaces with) every live member of that team. */}
      {teams.length > 0 && (
        <div>
          <label className="text-xs uppercase text-[var(--text-tertiary)]">Quick add by team</label>
          <div className="mt-2 flex flex-wrap gap-2">
            {teams.map((t) => {
              const live = teamLiveMembers(t);
              const inSelected = live.filter((id) => selected.has(id)).length;
              const allIn = live.length > 0 && inSelected === live.length;
              const someIn = inSelected > 0 && !allIn;
              return (
                <div
                  key={t.team.team_id}
                  className={cn(
                    'flex items-center gap-1 border text-[11px] font-mono',
                    allIn
                      ? 'border-[var(--border-strong)] bg-[var(--bg-elevated)]'
                      : someIn
                        ? 'border-[var(--border-default)]'
                        : 'border-[var(--border-subtle)]'
                  )}
                >
                  <button
                    onClick={() => (allIn ? dropTeam(t) : addTeam(t))}
                    className="px-2 py-1 hover:bg-[var(--bg-tertiary)] flex items-center gap-1"
                    title={allIn ? `Deselect all members of "${t.team.name}"` : `Add all members of "${t.team.name}"`}
                  >
                    {t.team.color && (
                      <span className="w-2 h-2 rounded-full" style={{ backgroundColor: t.team.color }} />
                    )}
                    <span>{t.team.name}</span>
                    <span className="text-[var(--text-tertiary)]">
                      ({inSelected}/{live.length})
                    </span>
                  </button>
                  <button
                    onClick={() => replaceWithTeam(t)}
                    className="px-1.5 py-1 hover:bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] border-l border-[var(--border-subtle)]"
                    title={`Replace selection with "${t.team.name}" members`}
                  >
                    only
                  </button>
                </div>
              );
            })}
            <button
              onClick={() => onBulkSet(new Set(agents.map((a) => a.agent_id)))}
              className="px-2 py-1 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)] text-[11px] font-mono"
            >
              all agents ({agents.length})
            </button>
            <button
              onClick={() => onBulkSet(new Set())}
              className="px-2 py-1 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)] text-[11px] font-mono"
            >
              clear
            </button>
          </div>
        </div>
      )}

      <div>
        <label className="text-xs uppercase text-[var(--text-tertiary)]">
          Agents to include · {selected.size}/{agents.length}
        </label>
        <div className="mt-2 grid grid-cols-2 gap-2">
          {agents.map((a) => {
            const checked = selected.has(a.agent_id);
            return (
              <button
                key={a.agent_id}
                onClick={() => onToggle(a.agent_id)}
                className={cn(
                  'text-left p-3 border transition-colors',
                  checked
                    ? 'bg-[var(--bg-elevated)] border-[var(--border-strong)]'
                    : 'bg-[var(--bg-secondary)] border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]'
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="text-sm font-mono truncate">{a.name || a.agent_id}</div>
                  {checked && <Check className="w-3.5 h-3.5" />}
                </div>
                <div className="text-[10px] text-[var(--text-tertiary)] truncate">{a.agent_id}</div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

type SkillEntryT = { name: string; dirName: string; path: string };

function SkillsTab({
  agents, userId, skillsForAgents, skillArchives, skillChoices, mode, onChange, onAfterBackup,
}: {
  agents: any[];
  userId: string;
  skillsForAgents: Record<string, SkillEntryT[]>;
  skillArchives: SkillArchiveRecord[];
  skillChoices: Record<string, SkillExportSpec>;
  mode: 'full' | 'custom';
  onChange: (agentId: string, dirName: string, spec: SkillExportSpec) => void;
  onAfterBackup: () => void;
}) {
  if (agents.length === 0) {
    return (
      <div className="text-sm text-[var(--text-tertiary)]">
        Select agents in the previous tab — their skills will appear here.
      </div>
    );
  }
  const isReadOnly = mode === 'full';
  const allLoaded = agents.every((a) => a.agent_id in skillsForAgents);
  const totalSkills = agents.reduce((s, a) => s + (skillsForAgents[a.agent_id]?.length || 0), 0);
  if (!allLoaded && totalSkills === 0) {
    return (
      <div className="text-sm text-[var(--text-tertiary)] flex items-center gap-2">
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
        Loading skills for selected agents…
      </div>
    );
  }
  if (allLoaded && totalSkills === 0) {
    return (
      <div className="text-sm text-[var(--text-tertiary)]">
        None of the selected agents have any skills installed.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="space-y-2 text-xs text-[var(--text-tertiary)] leading-relaxed border-l-2 border-[var(--accent-primary)]/40 pl-3">
        <p>
          <strong className="text-[var(--text-secondary)]">What you pick here:</strong> which
          skills (and MCP servers) ship with each agent, and <em>how</em> they ship —
          GitHub URL (recipient pulls fresh), Zip (frozen archive), or Full Copy
          (full directory tree, env vars and study results included).
        </p>
        <p>
          The same skill name can live on multiple agents independently — each row has its
          own <code>.skill_meta.json</code>, <code>env_config</code>, <code>study_result</code>.
          Pick per-(agent, skill).
          {isReadOnly && ' Full snapshot pinned every skill to Full Copy — read-only here.'}
        </p>
      </div>
      {agents.map((a) => {
        const loaded = a.agent_id in skillsForAgents;
        const skills = skillsForAgents[a.agent_id] || [];
        if (!loaded) {
          return (
            <details key={a.agent_id} className="border border-[var(--border-default)]">
              <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center gap-2">
                <Loader2 className="w-3 h-3 animate-spin text-[var(--text-tertiary)]" />
                {a.name || a.agent_id} — loading skills…
              </summary>
            </details>
          );
        }
        if (skills.length === 0) {
          return (
            <details key={a.agent_id} className="border border-[var(--border-default)]">
              <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)]">
                {a.name || a.agent_id} — no skills installed
              </summary>
            </details>
          );
        }
        return (
          <details key={a.agent_id} open className="border border-[var(--border-default)]">
            <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center justify-between">
              <span>{a.name || a.agent_id}</span>
              <span className="text-[10px] text-[var(--text-tertiary)]">
                {skills.length} skill{skills.length === 1 ? '' : 's'}
              </span>
            </summary>
            <div className="p-2 space-y-2">
              {skills.map((sk: SkillEntryT) => {
                // Use the skill's frontmatter `name` to look up archives
                // (skill_archives is keyed by name, not dir). The dir-based
                // key is what disambiguates this row from other same-named
                // siblings within the same agent.
                const arch = skillArchives.find((aa) => aa.skill_name === sk.name);
                const choice = skillChoices[`${a.agent_id}::${sk.dirName}`];
                const hasUrl = !!arch?.source_url;
                const hasZip = !!arch?.archive_path;
                const setMethod = (spec: SkillExportSpec) => {
                  if (isReadOnly) return;
                  // Pass dirName as the unique key. spec carries skill_dir
                  // so backend knows which physical directory to package.
                  onChange(a.agent_id, sk.dirName, { ...spec, skill_dir: sk.dirName });
                };
                // Detect duplicate-name siblings (e.g. two `arena` dirs under
                // this agent). Show the dir name to disambiguate.
                const sameNameCount = skills.filter((s) => s.name === sk.name).length;
                return (
                  <div key={sk.dirName} className="border border-[var(--border-subtle)] p-3">
                    <div className="flex items-center justify-between mb-2">
                      <div>
                        <div className="text-sm font-mono">{sk.name}</div>
                        <div className="text-[10px] text-[var(--text-tertiary)]">
                          {sameNameCount > 1 && (
                            <span className="text-[var(--color-yellow-500)] mr-1">dir: {sk.dirName}</span>
                          )}
                          {arch?.source_type
                            ? `archived (${arch.source_type})`
                            : 'no archive registered'}
                        </div>
                      </div>
                      {choice?.install_method === 'full_copy' && (
                        <span className="text-[10px] px-1.5 py-0.5 border border-[var(--color-yellow-500)] text-[var(--color-yellow-500)]">
                          contains_secrets
                        </span>
                      )}
                    </div>
                    <div className={cn('grid grid-cols-4 gap-2', isReadOnly && 'opacity-60 pointer-events-none')}>
                      <RadioCard
                        label="URL install"
                        desc={hasUrl ? `${arch?.source_url}` : (choice?.install_method === 'url' && choice.source_url ? `(manual) ${choice.source_url}` : 'No URL recorded')}
                        disabled={isReadOnly}
                        active={choice?.install_method === 'url'}
                        onClick={() => setMethod({
                          skill_name: sk.name, install_method: 'url',
                          source_url: arch?.source_url || choice?.source_url || '',
                          source_type: 'github', branch: 'main',
                        })}
                      />
                      <RadioCard
                        label="Zip install"
                        desc={hasZip ? `archive ${arch?.archive_path?.split('/').pop()}` : (choice?.install_method === 'zip' && choice.manual_zip_path ? `(manual) ${choice.manual_zip_path}` : 'No archive')}
                        disabled={isReadOnly}
                        active={choice?.install_method === 'zip'}
                        onClick={() => setMethod({
                          skill_name: sk.name, install_method: 'zip',
                          archive_path: arch?.archive_path || choice?.archive_path || undefined,
                          manual_zip_path: choice?.manual_zip_path,
                        })}
                      />
                      <RadioCard
                        label="Full copy"
                        desc="⚠ includes wallets/credentials"
                        active={choice?.install_method === 'full_copy'}
                        disabled={isReadOnly}
                        onClick={() => setMethod({ skill_name: sk.name, install_method: 'full_copy' })}
                      />
                      <RadioCard
                        label="Skip"
                        desc="Don't include this skill"
                        active={choice?.install_method === 'skip'}
                        disabled={isReadOnly}
                        onClick={() => setMethod({ skill_name: sk.name, install_method: 'skip' })}
                      />
                    </div>
                    {/* Manual URL fill */}
                    {!isReadOnly && choice?.install_method === 'url' && !hasUrl && (
                      <div className="mt-2 flex items-center gap-2">
                        <span className="text-[10px] text-[var(--text-tertiary)] shrink-0">GitHub URL:</span>
                        <input
                          type="text"
                          placeholder="https://github.com/owner/repo"
                          defaultValue={choice.source_url || ''}
                          onChange={(e) => setMethod({
                            skill_name: sk.name, install_method: 'url',
                            source_url: e.target.value, source_type: 'github',
                            branch: choice.branch || 'main',
                          })}
                          className="flex-1 px-2 py-1 text-xs font-mono bg-[var(--bg-tertiary)] border border-[var(--border-subtle)] focus:outline-none"
                        />
                        <input
                          type="text"
                          placeholder="branch"
                          defaultValue={choice.branch || 'main'}
                          onChange={(e) => setMethod({
                            skill_name: sk.name, install_method: 'url',
                            source_url: choice.source_url || '', source_type: 'github',
                            branch: e.target.value || 'main',
                          })}
                          className="w-24 px-2 py-1 text-xs font-mono bg-[var(--bg-tertiary)] border border-[var(--border-subtle)] focus:outline-none"
                        />
                      </div>
                    )}
                    {/* Manual zip upload */}
                    {!isReadOnly && choice?.install_method === 'zip' && !hasZip && (
                      <div className="mt-2 flex items-center gap-2">
                        <span className="text-[10px] text-[var(--text-tertiary)] shrink-0">Upload zip:</span>
                        <input
                          type="file"
                          accept=".zip"
                          onChange={async (e) => {
                            const f = e.target.files?.[0];
                            if (!f) return;
                            try {
                              await api.uploadSkillArchive({ skillName: sk.name, sourceType: 'zip', file: f });
                              onAfterBackup();
                            } catch (err) {
                              console.error('manual zip upload failed', err);
                            }
                          }}
                          className="text-[11px]"
                        />
                        <span className="text-[10px] text-[var(--text-tertiary)]">
                          uploads to your skill_archives so subsequent exports reuse it
                        </span>
                      </div>
                    )}
                    {!hasUrl && !hasZip && !isReadOnly && choice?.install_method !== 'url' && choice?.install_method !== 'zip' && (
                      <div className="mt-2 text-[10px] text-[var(--text-tertiary)] flex items-start gap-1.5">
                        <AlertTriangle className="w-3 h-3 mt-0.5 text-[var(--color-yellow-500)] shrink-0" />
                        <span className="flex-1">
                          This skill has no archive. Pick URL/Zip to fill in a source manually,
                          ask the agent to back it up, choose Full copy (含 credentials), or Skip.
                        </span>
                        <AskAgentToBackupButton
                          agentIds={[a.agent_id]}
                          userId={userId}
                          skillName={sk.name}
                          onDone={onAfterBackup}
                        />
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function AskAgentToBackupButton({
  agentIds, userId, skillName, onDone,
}: { agentIds: string[]; userId: string; skillName: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      onClick={async () => {
        if (agentIds.length === 0) return;
        setBusy(true);
        // Pick the first selected agent — most common case is one-skill-one-agent.
        const aid = agentIds[0];
        try {
          // Send a message via the standard chat WS endpoint asking the agent
          // to call its skill_backup MCP tool. We use the standard send-message
          // surface (post-message) so the request goes through the regular
          // agent loop.
          await fetch(`/api/agents/${encodeURIComponent(aid)}/messages`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              content: `Please back up the "${skillName}" skill for export — call skill_list_unbackedup() first to confirm it's missing, then choose the correct skill_backup_* MCP tool (skill_backup_from_github / _from_md / _from_local_zip) based on how you originally installed it. After the backup completes, tell me you're done.`,
              user_id: userId,
            }),
          }).catch(() => {});
          // Note: we don't wait for the agent to actually run; just kick the
          // message and ask the user to refresh archives in a few seconds.
          setTimeout(onDone, 3000);
        } finally {
          setBusy(false);
        }
      }}
      disabled={busy || agentIds.length === 0}
      className="text-[10px] px-2 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
      title="Send a message to the first selected agent asking it to call skill_backup_*"
    >
      {busy ? '…' : 'Ask agent to back up'}
    </button>
  );
}

function HistoryTab({
  agents, historyByAgent, excludedNarratives, includedEvents, excludedJobs,
  onToggleNarrative, onToggleEvent, onToggleJob,
  onSelectAllNarratives, onSelectNoneNarratives,
  onSelectAllEventsInNarrative, onSelectNoneEventsInNarrative,
  onSelectAllJobsInNarrative, onSelectNoneJobsInNarrative,
  eventDisplayLimit, eventPageSize, onShowMoreEvents, onShowAllEvents,
  chatHistoryEnabled,
}: {
  agents: any[];
  historyByAgent: Record<string, ChatHistoryNarrative[]>;
  excludedNarratives: Record<string, Set<string>>;
  /** Per-narrative inclusion set. Empty / missing entry = ship 0
   *  events for that narrative (opt-in default, see top-of-page state
   *  declaration for the full rationale). */
  includedEvents: Record<string, Set<string>>;
  excludedJobs: Record<string, Set<string>>;
  onToggleNarrative: (aid: string, nid: string) => void;
  onToggleEvent: (nid: string, eid: string) => void;
  onToggleJob: (nid: string, jid: string) => void;
  onSelectAllNarratives: (aid: string) => void;
  onSelectNoneNarratives: (aid: string) => void;
  onSelectAllEventsInNarrative: (nid: string, allEventIds: string[]) => void;
  onSelectNoneEventsInNarrative: (nid: string) => void;
  onSelectAllJobsInNarrative: (nid: string) => void;
  onSelectNoneJobsInNarrative: (nid: string, allJobIds: string[]) => void;
  /** Per-narrative cap for how many event checkboxes to render. Defaults
   *  to {@link eventPageSize}; "Show more" / "Show all" buttons mutate
   *  the entry via the corresponding handlers. */
  eventDisplayLimit: Record<string, number>;
  eventPageSize: number;
  onShowMoreEvents: (nid: string, total: number) => void;
  onShowAllEvents: (nid: string, total: number) => void;
  /** Tracks the "Include chat history" toggle in the Bundle Notes
   *  section. When false, the event list (and the message body inside
   *  each narrative.json) is dropped from the bundle, but narrative
   *  skeletons + jobs are still selectable here. */
  chatHistoryEnabled: boolean;
}) {
  if (agents.length === 0) {
    return (<div className="text-sm text-[var(--text-tertiary)]">Select agents first.</div>);
  }
  return (
    <div className="space-y-3">
      <div className="space-y-2 text-xs text-[var(--text-tertiary)] leading-relaxed border-l-2 border-[var(--accent-primary)]/40 pl-3">
        <p>
          <strong className="text-[var(--text-secondary)]">What you pick here:</strong> which
          narratives (the agent's "topic threads") ship in the bundle, and — for each one —
          which individual events (chat turns) and jobs ride along with it.
        </p>
        <p>
          <strong className="text-[var(--text-secondary)]">Defaults:</strong> all narratives
          are included; <em>no events</em> are included unless you tick them; all jobs under
          a kept narrative ship along.
        </p>
        {!chatHistoryEnabled && (
          <p className="text-[var(--color-yellow-500)]">
            <strong>Chat history is disabled</strong> (toggle in the Bundle Notes section).
            Narrative skeletons + jobs still ship; events and message bodies do not — your
            per-event picks below are saved but won't take effect until you re-enable.
          </p>
        )}
      </div>
      {agents.map((a) => {
        // Distinguish "not loaded yet" from "loaded, empty":
        //   - undefined  → fetch still in flight, show Loading…
        //   - []         → fetched, this agent really has no narratives
        //   - non-empty  → render as usual
        const loaded = a.agent_id in historyByAgent;
        const narrs = historyByAgent[a.agent_id] || [];
        const exNars = excludedNarratives[a.agent_id] || new Set();
        return (
          <details key={a.agent_id} open className="border border-[var(--border-default)]">
            <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center justify-between">
              <span>{a.name || a.agent_id}</span>
              <div className="flex items-center gap-2">
                {!loaded ? (
                  <span className="text-[10px] text-[var(--text-tertiary)] flex items-center gap-1">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    Loading…
                  </span>
                ) : (
                  <span className="text-[10px] text-[var(--text-tertiary)]">
                    {narrs.length - exNars.size} / {narrs.length} narratives
                  </span>
                )}
                <button
                  onClick={(e) => { e.preventDefault(); onSelectAllNarratives(a.agent_id); }}
                  disabled={!loaded}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)] disabled:opacity-40"
                >
                  Select all
                </button>
                <button
                  onClick={(e) => { e.preventDefault(); onSelectNoneNarratives(a.agent_id); }}
                  disabled={!loaded}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)] disabled:opacity-40"
                >
                  Select none
                </button>
              </div>
            </summary>
            <div className="p-2 max-h-[480px] overflow-y-auto space-y-2">
              {!loaded && (
                <div className="px-2 py-3 text-xs text-[var(--text-tertiary)] flex items-center gap-2">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>Loading narratives + events + jobs from this agent…</span>
                </div>
              )}
              {loaded && narrs.length === 0 && (
                <div className="px-2 py-3 text-xs text-[var(--text-tertiary)]">No narratives.</div>
              )}
              {narrs.map((n) => {
                const narExcluded = exNars.has(n.narrative_id);
                const inEvts = includedEvents[n.narrative_id] || new Set();
                const exJobs = excludedJobs[n.narrative_id] || new Set();
                const isOrphanJobsRow = n.narrative_id === '__orphan_jobs__';
                return (
                  <div key={n.narrative_id} className={cn(
                    'border border-[var(--border-subtle)]',
                    narExcluded && 'opacity-50'
                  )}>
                    <div className="px-3 py-2 flex items-center gap-2 bg-[var(--bg-secondary)]">
                      {!isOrphanJobsRow && (
                        <input
                          type="checkbox"
                          checked={!narExcluded}
                          onChange={() => onToggleNarrative(a.agent_id, n.narrative_id)}
                        />
                      )}
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-mono truncate">{n.title}</div>
                        {(n.description || n.current_summary) && (
                          <div className="text-[11px] text-[var(--text-secondary)] line-clamp-2">
                            {n.description || n.current_summary}
                          </div>
                        )}
                        <div className="text-[10px] text-[var(--text-tertiary)] flex flex-wrap gap-x-2">
                          {!isOrphanJobsRow && (
                            <span>
                              {inEvts.size} / {n.events.length} events
                            </span>
                          )}
                          {n.jobs.length > 0 && (
                            <span>{n.jobs.length - exJobs.size} / {n.jobs.length} jobs</span>
                          )}
                          {n.type && <span>{n.type}</span>}
                          {(n.instances_count ?? 0) > 0 && (
                            <span>{n.instances_count} instance(s)</span>
                          )}
                          {n.created_at && <span>{(n.created_at || '').slice(0, 10)}</span>}
                          <span className="text-[var(--text-tertiary)]/70">{n.narrative_id}</span>
                        </div>
                      </div>
                      {!narExcluded && (n.events.length > 0 || n.jobs.length > 0) && (
                        <div className="flex items-center gap-1 shrink-0">
                          {chatHistoryEnabled && n.events.length > 0 && (
                            <>
                              <button
                                onClick={(e) => { e.stopPropagation(); onSelectAllEventsInNarrative(n.narrative_id, n.events.map((x) => x.event_id)); }}
                                className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                              >
                                All events
                              </button>
                              <button
                                onClick={(e) => { e.stopPropagation(); onSelectNoneEventsInNarrative(n.narrative_id); }}
                                className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                              >
                                No events
                              </button>
                            </>
                          )}
                          {n.jobs.length > 0 && (
                            <>
                              <button
                                onClick={(e) => { e.stopPropagation(); onSelectAllJobsInNarrative(n.narrative_id); }}
                                className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                              >
                                All jobs
                              </button>
                              <button
                                onClick={(e) => { e.stopPropagation(); onSelectNoneJobsInNarrative(n.narrative_id, n.jobs.map((x) => x.job_id)); }}
                                className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                              >
                                No jobs
                              </button>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                    {chatHistoryEnabled && !narExcluded && n.events.length > 0 && (() => {
                      // Render-side pagination. Events are pre-sorted
                      // newest → oldest so the visible slice is the
                      // most recent activity, which is what users
                      // typically want to inspect when picking. Events
                      // are opt-in: a checkbox shows checked only when
                      // the user has explicitly added the event_id to
                      // includedEvents[nid].
                      const limit = eventDisplayLimit[n.narrative_id] ?? eventPageSize;
                      const visible = n.events.slice(0, limit);
                      const hidden = n.events.length - visible.length;
                      return (
                        <div className="border-t border-[var(--border-subtle)]">
                          {visible.map((e) => {
                            const evIncluded = inEvts.has(e.event_id);
                            return (
                              <label key={e.event_id} className={cn(
                                'flex items-start gap-2 px-3 py-1 text-xs hover:bg-[var(--bg-tertiary)]',
                                !evIncluded && 'opacity-50'
                              )}>
                                <input
                                  type="checkbox"
                                  checked={evIncluded}
                                  onChange={() => onToggleEvent(n.narrative_id, e.event_id)}
                                  className="mt-0.5"
                                />
                                <div className="flex-1 min-w-0">
                                  <div className="font-mono text-[10px] text-[var(--text-tertiary)]">
                                    {e.trigger || 'event'} · {(e.created_at || '').slice(0, 19)}
                                  </div>
                                  <div className="truncate">{e.preview || '(no preview)'}</div>
                                </div>
                              </label>
                            );
                          })}
                          {hidden > 0 && (
                            <div className="px-3 py-1.5 flex items-center justify-between gap-2 text-[10px] bg-[var(--bg-secondary)]/40 border-t border-[var(--border-subtle)]">
                              <span className="text-[var(--text-tertiary)]">
                                Showing {visible.length} / {n.events.length} events
                                (oldest {hidden} hidden)
                              </span>
                              <div className="flex items-center gap-1.5 shrink-0">
                                <button
                                  onClick={(ev) => { ev.stopPropagation(); onShowMoreEvents(n.narrative_id, n.events.length); }}
                                  className="px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                                >
                                  Show {Math.min(eventPageSize, hidden)} more
                                </button>
                                <button
                                  onClick={(ev) => { ev.stopPropagation(); onShowAllEvents(n.narrative_id, n.events.length); }}
                                  className="px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                                >
                                  Show all ({n.events.length})
                                </button>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })()}
                    {!narExcluded && n.jobs.length > 0 && (
                      <div className="border-t border-[var(--border-subtle)] bg-[var(--bg-tertiary)]/30">
                        <div className="px-3 py-1 text-[10px] text-[var(--text-tertiary)] uppercase tracking-widest">
                          Jobs ({n.jobs.length - exJobs.size} / {n.jobs.length} included)
                        </div>
                        {n.jobs.map((j) => {
                          const jobExcluded = exJobs.has(j.job_id);
                          return (
                            <label key={j.job_id} className={cn(
                              'flex items-start gap-2 px-3 py-1 text-xs hover:bg-[var(--bg-tertiary)]',
                              jobExcluded && 'opacity-40'
                            )}>
                              <input
                                type="checkbox"
                                checked={!jobExcluded}
                                onChange={() => onToggleJob(n.narrative_id, j.job_id)}
                                className="mt-0.5"
                              />
                              <div className="flex-1 min-w-0">
                                <div className="font-mono text-[10px] text-[var(--text-tertiary)]">
                                  {j.job_type || 'job'} · {j.status || ''}
                                </div>
                                <div className="truncate">{j.title || j.job_id}</div>
                                {j.description && (
                                  <div className="text-[10px] text-[var(--text-tertiary)] truncate">{j.description}</div>
                                )}
                              </div>
                            </label>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function SensitiveZipConfirmModal({
  hits, onCancel, onAccept,
}: {
  hits: { skill: string; hits: string[] }[];
  onCancel: () => void;
  onAccept: () => void;
}) {
  const [confirmText, setConfirmText] = useState('');
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center backdrop-blur-sm"
      style={{ background: 'var(--nm-backdrop)' }}
    >
      <div className="w-[520px] max-w-[95vw] bg-[var(--bg-primary)] border-2 border-[var(--color-red-500)] flex flex-col">
        <div className="px-5 py-3 border-b border-[var(--border-default)] bg-[var(--color-red-500)]/10">
          <div className="flex items-center gap-2 text-[var(--color-red-500)]">
            <AlertTriangle className="w-5 h-5" />
            <h2 className="font-mono text-sm">Sensitive files detected in skill zip</h2>
          </div>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <p>
            One or more zip-archived skills in your bundle contain files matching sensitive
            path patterns (.env, .key, wallet.json, credentials.json, etc.).
            <strong> If you proceed, recipients will receive these files.</strong>
          </p>
          <ul className="list-disc list-inside space-y-1 text-xs font-mono bg-[var(--bg-tertiary)] p-3 max-h-[200px] overflow-y-auto">
            {hits.map((w, i) => (
              <li key={i}>
                <span className="text-[var(--color-red-500)]">{w.skill}</span>:{' '}
                {(w.hits || []).slice(0, 5).join(', ')}
                {(w.hits || []).length > 5 && ` (+${(w.hits || []).length - 5} more)`}
              </li>
            ))}
          </ul>
          <p className="text-xs text-[var(--text-secondary)]">
            Type <strong className="font-mono">SHARE SECRETS</strong> below to confirm you understand
            and want to ship these files anyway.
          </p>
          <input
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="Type SHARE SECRETS to confirm"
            className="w-full px-3 py-2 text-sm font-mono bg-[var(--bg-tertiary)] border border-[var(--border-default)] focus:outline-none"
          />
        </div>
        <div className="px-5 py-3 border-t border-[var(--border-default)] flex justify-end gap-2">
          <Button onClick={onCancel} variant="ghost" size="sm">Cancel</Button>
          <Button
            onClick={onAccept}
            disabled={confirmText !== 'SHARE SECRETS'}
            size="sm"
            className="bg-[var(--color-red-500)] text-white hover:bg-[var(--color-red-500)]/80"
          >
            Ship anyway
          </Button>
        </div>
      </div>
    </div>
  );
}

function RadioCard({ label, desc, disabled, active, onClick }: { label: string; desc: string; disabled?: boolean; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        'p-2 text-left border text-xs transition-colors',
        disabled && 'opacity-40 cursor-not-allowed',
        active
          ? 'border-[var(--text-primary)] bg-[var(--bg-elevated)]'
          : 'border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]'
      )}
    >
      <div className="font-mono">{label}</div>
      <div className="text-[10px] text-[var(--text-tertiary)] mt-1 break-all">{desc}</div>
    </button>
  );
}

function SocialTab({
  entitiesByAgent, selectedByAgent, pageByAgent, onTogglePage,
  selectedPageByAgent, onToggleSelectedPage,
  agents, onToggle, onBulkSet,
}: {
  entitiesByAgent: Record<string, SocialEntity[]>;
  selectedByAgent: Record<string, Set<string>>;
  pageByAgent: Record<string, number>;
  onTogglePage: (aid: string, page: number) => void;
  selectedPageByAgent: Record<string, number>;
  onToggleSelectedPage: (aid: string, page: number) => void;
  agents: any[];
  onToggle: (aid: string, eid: string) => void;
  onBulkSet: (aid: string, ids: string[]) => void;
  selectedTeam?: string;
  teams?: TeamWithMembers[];
}) {
  // Unified pagination — both the "All" and "Selected" panes show 30/page (P10).
  const PAGE_SIZE = 30;
  if (agents.length === 0) return (
    <div className="text-sm text-[var(--text-tertiary)]">Select agents first.</div>
  );
  return (
    <div className="space-y-4">
      <div className="space-y-2 text-xs text-[var(--text-tertiary)] leading-relaxed border-l-2 border-[var(--accent-primary)]/40 pl-3">
        <p>
          <strong className="text-[var(--text-secondary)]">What you pick here:</strong> the
          "people / organizations / agents this agent remembers" — its social network
          entities. These travel with the bundle so the recipient gets the agent's
          context about who's who.
        </p>
        <p>
          Edges pointing at agents <em>outside</em> the bundle's agent closure are
          dropped automatically. By default, all entities matching the team scope are
          preselected; uncheck any you don't want shared.
        </p>
      </div>
      {agents.map((a) => {
        const loaded = a.agent_id in entitiesByAgent;
        const list = (entitiesByAgent[a.agent_id] || []).slice().sort((x, y) =>
          (x.entity_name || x.entity_id).localeCompare(y.entity_name || y.entity_id)
        );
        const selected = selectedByAgent[a.agent_id] || new Set<string>();

        // Left pane (All entities) pagination
        const page = pageByAgent[a.agent_id] || 0;
        const totalPages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
        const safePage = Math.min(page, totalPages - 1);
        const slice = list.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);

        // Right pane (Selected entities) pagination — sorted same way
        const selectedList = list.filter((e) => selected.has(e.entity_id));
        const selPage = selectedPageByAgent[a.agent_id] || 0;
        const selTotalPages = Math.max(1, Math.ceil(selectedList.length / PAGE_SIZE));
        const safeSelPage = Math.min(selPage, selTotalPages - 1);
        const selSlice = selectedList.slice(safeSelPage * PAGE_SIZE, (safeSelPage + 1) * PAGE_SIZE);

        if (!loaded) {
          return (
            <details key={a.agent_id} className="border border-[var(--border-default)]">
              <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center gap-2">
                <Loader2 className="w-3 h-3 animate-spin text-[var(--text-tertiary)]" />
                {a.name || a.agent_id} — loading social network entities…
              </summary>
            </details>
          );
        }
        return (
          <details key={a.agent_id} open className="border border-[var(--border-default)]">
            <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center justify-between">
              <span>{a.name || a.agent_id}</span>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-[var(--text-tertiary)]">
                  {selected.size} / {list.length} selected
                </span>
                <button
                  onClick={(e) => { e.preventDefault(); onBulkSet(a.agent_id, list.map((x) => x.entity_id)); }}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                  disabled={list.length === 0}
                >
                  Select all
                </button>
                <button
                  onClick={(e) => { e.preventDefault(); onBulkSet(a.agent_id, []); }}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                  disabled={selected.size === 0}
                >
                  Select none
                </button>
              </div>
            </summary>
            <div className="grid grid-cols-2 divide-x divide-[var(--border-subtle)]">
              {/* Left: all entities (paged 30/page) */}
              <div className="p-2">
                <div className="text-[10px] text-[var(--text-tertiary)] mb-1 px-2">
                  All entities (sort by name) — {list.length} total
                </div>
                {slice.length === 0 && (
                  <div className="px-2 py-3 text-xs text-[var(--text-tertiary)]">No entities for this agent.</div>
                )}
                {slice.map((e) => {
                  const isSel = selected.has(e.entity_id);
                  return (
                    <div key={e.entity_id} className="flex items-start gap-2 px-2 py-1 hover:bg-[var(--bg-tertiary)]">
                      <input type="checkbox" checked={isSel} onChange={() => onToggle(a.agent_id, e.entity_id)} className="mt-1" />
                      <div className="flex-1 min-w-0">
                        <details>
                          <summary className="text-xs font-mono cursor-pointer truncate">
                            {e.entity_name || e.entity_id}{' '}
                            <span className="text-[9px] text-[var(--text-tertiary)]">[{e.entity_type}]</span>
                          </summary>
                          <div className="mt-1 ml-2 text-[10px] text-[var(--text-tertiary)] space-y-0.5">
                            <div>id: {e.entity_id}</div>
                            {e.entity_description && <div>desc: {e.entity_description}</div>}
                            {(e.tags || []).length > 0 && <div>tags: {(e.tags || []).join(', ')}</div>}
                          </div>
                        </details>
                      </div>
                    </div>
                  );
                })}
                {totalPages > 1 && (
                  <Pager
                    page={safePage}
                    total={totalPages}
                    onPage={(p) => onTogglePage(a.agent_id, p)}
                  />
                )}
              </div>
              {/* Right: selected (paged 30/page) */}
              <div className="p-2">
                <div className="text-[10px] text-[var(--text-tertiary)] mb-1 px-2">
                  Selected (will be packaged) — {selectedList.length} total
                </div>
                {selSlice.length === 0 && (
                  <div className="px-2 py-3 text-xs text-[var(--text-tertiary)]">Nothing selected.</div>
                )}
                {selSlice.map((e) => (
                  <div key={e.entity_id} className="flex items-center gap-2 px-2 py-1 text-xs font-mono">
                    <Check className="w-3 h-3 text-[var(--color-green-500)] shrink-0" />
                    <span className="flex-1 truncate">{e.entity_name || e.entity_id}</span>
                    <span className="text-[9px] text-[var(--text-tertiary)]">[{e.entity_type}]</span>
                    <button onClick={() => onToggle(a.agent_id, e.entity_id)} className="text-[var(--color-red-500)] text-[10px]">remove</button>
                  </div>
                ))}
                {selTotalPages > 1 && (
                  <Pager
                    page={safeSelPage}
                    total={selTotalPages}
                    onPage={(p) => onToggleSelectedPage(a.agent_id, p)}
                  />
                )}
              </div>
            </div>
          </details>
        );
      })}
    </div>
  );
}

function Pager({ page, total, onPage }: { page: number; total: number; onPage: (p: number) => void }) {
  return (
    <div className="flex items-center justify-center gap-1 py-2 text-xs">
      <button
        onClick={() => onPage(Math.max(0, page - 1))}
        disabled={page === 0}
        className="px-2 py-0.5 border border-[var(--border-subtle)] disabled:opacity-30"
      >‹</button>
      <span>{page + 1} / {total}</span>
      <button
        onClick={() => onPage(Math.min(total - 1, page + 1))}
        disabled={page >= total - 1}
        className="px-2 py-0.5 border border-[var(--border-subtle)] disabled:opacity-30"
      >›</button>
    </div>
  );
}

function BusTab({
  agents, channels, selected, mode, onToggle, onSelectAll, onSelectNone,
}: {
  agents: any[];
  channels: BusChannel[] | null;
  selected: Set<string>;
  mode: 'full' | 'custom';
  onToggle: (channelId: string) => void;
  onSelectAll: () => void;
  onSelectNone: () => void;
}) {
  const readOnly = mode === 'full';
  const agentNameById: Record<string, string> = useMemo(() => {
    const m: Record<string, string> = {};
    for (const a of agents) m[a.agent_id] = a.name || a.agent_id;
    return m;
  }, [agents]);

  if (channels === null) {
    return (
      <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)] font-mono py-6">
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
        Loading message bus channels…
      </div>
    );
  }
  if (agents.length === 0) {
    return <div className="text-xs text-[var(--text-tertiary)]">Pick at least one agent first.</div>;
  }
  if (channels.length === 0) {
    return (
      <div className="text-xs text-[var(--text-tertiary)] py-3">
        No message-bus channels exist for the selected agent closure. Channels
        ship only when you own them and at least one closure agent is a member.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="space-y-2 text-xs text-[var(--text-tertiary)] leading-relaxed border-l-2 border-[var(--accent-primary)]/40 pl-3">
        <p>
          <strong className="text-[var(--text-secondary)]">What you pick here:</strong> which
          Message Bus channels (agent-to-agent chat rooms) ship with the bundle. Only
          channels you own and that have at least one selected agent as a member appear.
        </p>
        <p>
          Default = all eligible channels selected. Channel rows are recreated on import
          with the recipient as owner; the historical messages inside them ride along
          with each agent's narrative + event selections (controlled in the Narratives
          & Events tab).
        </p>
      </div>
      <div className="flex items-center justify-between text-xs font-mono">
        <span className="text-[var(--text-tertiary)]">
          {channels.length} channel{channels.length === 1 ? '' : 's'} eligible · {selected.size} selected
        </span>
        {!readOnly && (
          <div className="flex items-center gap-1">
            <button
              onClick={onSelectAll}
              className="px-2 py-1 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
            >
              Select all
            </button>
            <button
              onClick={onSelectNone}
              className="px-2 py-1 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
            >
              Select none
            </button>
          </div>
        )}
      </div>
      <div className="text-[11px] text-[var(--text-tertiary)] leading-relaxed">
        Channels visible here are owned by you AND have at least one selected
        agent as a member. Unselected channels — and their messages — are
        excluded from the bundle. External members (agents outside this
        closure) get rewritten on import as references; members not in the
        closure don&apos;t come along.
      </div>
      <div className="border border-[var(--border-subtle)]">
        {channels.map((c) => {
          const checked = selected.has(c.channel_id);
          const externalMembers = c.all_member_ids.filter((mid) => !c.in_closure_member_ids.includes(mid));
          return (
            <label
              key={c.channel_id}
              className={cn(
                'flex items-start gap-3 px-3 py-2 border-b border-[var(--border-subtle)] last:border-b-0 cursor-pointer hover:bg-[var(--bg-tertiary)]',
                readOnly && 'cursor-default opacity-90'
              )}
            >
              <input
                type="checkbox"
                checked={checked}
                disabled={readOnly}
                onChange={() => !readOnly && onToggle(c.channel_id)}
                className="mt-1"
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-sm">{c.name || c.channel_id}</span>
                  {c.channel_type && (
                    <span className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] text-[var(--text-tertiary)] font-mono">
                      {c.channel_type}
                    </span>
                  )}
                  <span className="text-[10px] text-[var(--text-tertiary)] font-mono">
                    {c.message_count} msg{c.message_count === 1 ? '' : 's'}
                  </span>
                </div>
                <div className="text-[11px] text-[var(--text-tertiary)] mt-0.5 font-mono break-all">
                  {c.channel_id}
                </div>
                <div className="text-[11px] mt-1 text-[var(--text-secondary)]">
                  In closure: {c.in_closure_member_ids.map((mid) => agentNameById[mid] || mid.slice(0, 8)).join(', ') || '(none)'}
                  {externalMembers.length > 0 && (
                    <span className="text-[var(--text-tertiary)]">
                      {' '}· External: {externalMembers.length}
                    </span>
                  )}
                </div>
              </div>
            </label>
          );
        })}
      </div>
      {readOnly && (
        <div className="text-[11px] text-[var(--color-yellow-500)] flex items-center gap-1.5">
          <AlertTriangle className="w-3 h-3" />
          Full snapshot mode auto-includes every eligible channel. Switch to Custom to pick.
        </div>
      )}
    </div>
  );
}

function WorkspaceTab({
  filesByAgent, excludesByAgent, agents, onToggle, onBulkSet,
}: {
  filesByAgent: Record<string, { path: string; size: number; sensitive: boolean }[]>;
  excludesByAgent: Record<string, Set<string>>;
  agents: any[];
  onToggle: (aid: string, path: string) => void;
  // Dual-meaning bulk setter (see workspaceExcludes semantics):
  // include-all ⇒ excludesSet = {all sensitive paths}
  // include-non-sensitive-only ⇒ excludesSet = {} (default)
  // exclude-all ⇒ excludesSet = {all non-sensitive paths}
  onBulkSet: (aid: string, mode: 'all' | 'non-sensitive' | 'none') => void;
}) {
  if (agents.length === 0) return (<div className="text-sm text-[var(--text-tertiary)]">Select agents first.</div>);
  return (
    <div className="space-y-4">
      <div className="space-y-2 text-xs text-[var(--text-tertiary)] leading-relaxed border-l-2 border-[var(--accent-primary)]/40 pl-3">
        <p>
          <strong className="text-[var(--text-secondary)]">What you pick here:</strong> which
          files in each agent's workspace ride along inside the bundle's
          <code>workspace.tar.gz</code>. Sensitive paths (<code>.env</code>,
          <code>wallet.json</code>, <code>*.key</code>…) are <em>excluded by default</em>;
          tick them only if you mean to ship secrets.
        </p>
        <p>
          Non-sensitive files default to included; deselect any document you don't want
          recipients to see. Agent artifacts referenced by other tabs already travel here,
          so keep their source files in.
        </p>
      </div>
      {agents.map((a) => {
        const loaded = a.agent_id in filesByAgent;
        const files = filesByAgent[a.agent_id] || [];
        const excludes = excludesByAgent[a.agent_id] || new Set();
        if (!loaded) {
          return (
            <details key={a.agent_id} className="border border-[var(--border-default)]">
              <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center gap-2">
                <Loader2 className="w-3 h-3 animate-spin text-[var(--text-tertiary)]" />
                {a.name || a.agent_id} — loading workspace files…
              </summary>
            </details>
          );
        }
        const sensitiveCount = files.filter((f) => f.sensitive).length;
        const includedCount = files.filter((f) =>
          f.sensitive ? excludes.has(f.path) : !excludes.has(f.path)
        ).length;
        return (
          <details key={a.agent_id} open className="border border-[var(--border-default)]">
            <summary className="px-3 py-2 cursor-pointer text-sm font-mono bg-[var(--bg-secondary)] flex items-center justify-between">
              <span>{a.name || a.agent_id}</span>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-[var(--text-tertiary)]">
                  {includedCount} / {files.length} files included
                  {sensitiveCount > 0 && ` · ${sensitiveCount} sensitive`}
                </span>
                <button
                  onClick={(e) => { e.preventDefault(); onBulkSet(a.agent_id, 'all'); }}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                  disabled={files.length === 0}
                >
                  Include all
                </button>
                <button
                  onClick={(e) => { e.preventDefault(); onBulkSet(a.agent_id, 'non-sensitive'); }}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                  disabled={sensitiveCount === 0 && includedCount === files.length - 0}
                  title="Include all NON-sensitive files; sensitive files default-skipped"
                >
                  Defaults
                </button>
                <button
                  onClick={(e) => { e.preventDefault(); onBulkSet(a.agent_id, 'none'); }}
                  className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)]"
                  disabled={files.length === 0}
                >
                  Exclude all
                </button>
              </div>
            </summary>
            <div className="p-2 max-h-[320px] overflow-y-auto">
              {files.length === 0 && (
                <div className="px-2 py-3 text-xs text-[var(--text-tertiary)]">No workspace files reported by API.</div>
              )}
              {files.map((f) => {
                const sensitive = f.sensitive;
                const excluded = excludes.has(f.path);
                // Default include rules:
                //   - non-sensitive: included unless in `excludes` set
                //   - sensitive: excluded by default; included ONLY if user
                //     explicitly clicked through the confirm dialog (we track
                //     that as a presence in `excludes` toggled to false).
                // Implementation detail: `excludes` actually means "user
                // override". For non-sensitive files presence-in-excludes →
                // exclude. For sensitive files we INVERT the meaning: presence
                // means "user opted in" (included). This is hidden in the
                // toggle handler — sensitive files get a "really?" modal.
                const willBeIncluded = sensitive ? excluded : !excluded;
                return (
                  <label
                    key={f.path}
                    className={cn(
                      "flex items-center gap-2 px-2 py-1 hover:bg-[var(--bg-tertiary)]",
                      sensitive && "bg-[var(--color-yellow-500)]/10",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={willBeIncluded}
                      onChange={() => onToggle(a.agent_id, f.path)}
                    />
                    <span className={cn('text-xs font-mono flex-1 truncate', sensitive && 'text-[var(--color-yellow-500)]')}>
                      {f.path}
                    </span>
                    {sensitive && (
                      <span className="text-[9px] text-[var(--color-yellow-500)] uppercase tracking-wider font-mono">
                        {willBeIncluded ? 'sensitive — included' : 'sensitive — click to include'}
                      </span>
                    )}
                    <span className="text-[10px] text-[var(--text-tertiary)]">{Math.round(f.size / 1024)} KB</span>
                  </label>
                );
              })}
            </div>
          </details>
        );
      })}
    </div>
  );
}

// =============================================================================
// Review Summary Modal
// =============================================================================

function ReviewSummaryModal({
  summary, team, introMd, skills, warnings, onCancel, onConfirm, downloading,
  filename, mode,
}: any) {
  const skillStats = (skills || []).reduce(
    (acc: Record<string, number>, s: SkillExportSpec) => {
      const m = s.install_method || 'skip';
      acc[m] = (acc[m] || 0) + 1;
      return acc;
    },
    {} as Record<string, number>
  );
  const includedSkills = skills.filter(
    (s: SkillExportSpec) => s.install_method && s.install_method !== 'skip'
  ).length;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center backdrop-blur-sm"
      style={{ background: 'var(--nm-backdrop)' }}
    >
      <div className="w-[680px] max-w-[95vw] max-h-[90vh] bg-[var(--bg-primary)] border border-[var(--border-default)] flex flex-col">
        <div className="px-5 py-3 border-b border-[var(--border-default)] flex items-center justify-between">
          <h2 className="font-mono text-sm">Final review before download</h2>
          <span className="text-[10px] uppercase tracking-widest text-[var(--text-tertiary)]">
            {mode === 'full' ? 'Full snapshot' : 'Custom'}
          </span>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-4 text-sm font-mono">
          {/* Filename — show what will be downloaded */}
          <div className="text-[12px] flex items-center gap-2">
            <span className="text-[var(--text-tertiary)] uppercase tracking-widest text-[10px]">File:</span>
            <span className="text-[var(--text-primary)]">{filename || 'bundle.nxbundle'}</span>
          </div>
          <div>
            <div className="text-[var(--text-secondary)] uppercase text-xs mb-1">✓ Included</div>
            <ul className="list-disc list-inside text-[12px] text-[var(--text-secondary)] space-y-0.5">
              <li>{summary.agents} agent{summary.agents === 1 ? '' : 's'}</li>
              {team && <li>1 team "{team.team.name}"</li>}
              <li>
                {includedSkills} skill entr{includedSkills === 1 ? 'y' : 'ies'}:
                {' '}{skillStats.url || 0}× url,
                {' '}{skillStats.zip || 0}× zip,
                {' '}{skillStats.full_copy || 0}× full-copy
                {(skillStats.skip || 0) > 0 && `, ${skillStats.skip}× skip (NOT in bundle)`}
              </li>
              <li>{summary.socialEntities} social entit{summary.socialEntities === 1 ? 'y' : 'ies'}</li>
              <li>{summary.busChannels} message-bus channel{summary.busChannels === 1 ? '' : 's'}</li>
              <li>
                workspace files
                {mode === 'full'
                  ? ' (sensitive paths included)'
                  : ' (sensitive paths excluded by default — opt-in per file)'}
              </li>
              {introMd && <li>README.md ({introMd.length} chars)</li>}
            </ul>
          </div>
          <div>
            <div className="text-[var(--text-secondary)] uppercase text-xs mb-1">✗ Stripped</div>
            <ul className="list-disc list-inside text-[12px] text-[var(--text-secondary)] space-y-0.5">
              <li>LLM API keys, Lark OAuth tokens, password hashes</li>
              <li>workspace 外 (~/.config 等)</li>
              <li>env_config of all url/zip-installed skills</li>
            </ul>
          </div>
          {warnings.length > 0 && (
            <div>
              <div className="text-[var(--color-yellow-500)] uppercase text-xs mb-1 flex items-center gap-1">
                <AlertTriangle className="w-3 h-3" /> Warnings
              </div>
              <ul className="list-disc list-inside text-[12px] text-[var(--text-secondary)] space-y-0.5">
                {warnings.map((w: string, i: number) => <li key={i}>{w}</li>)}
              </ul>
            </div>
          )}
        </div>
        <div className="px-5 py-3 border-t border-[var(--border-default)] flex justify-end gap-2">
          <Button onClick={onCancel} variant="ghost" size="sm" disabled={downloading}>Cancel</Button>
          <Button onClick={onConfirm} size="sm" disabled={downloading} className="gap-1">
            {downloading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
            Download .nxbundle
          </Button>
        </div>
      </div>
    </div>
  );
}


// ── McpSection — rendered inside the Skills & MCP tab ─────────────────────
// MCP defaults to NONE selected (opt-in). User picks which URLs to ship; the
// importer writes them straight into mcp_urls on the recipient side with the
// connection_status field reset so the local poller validates from scratch.
function McpSection({
  agents, mcpsByAgent, selectedByAgent, mode, onToggle, onSelectAllForAgent, onClearForAgent,
}: {
  agents: any[];
  mcpsByAgent: Record<string, BundleMcpPreview[] | null>;
  selectedByAgent: Record<string, Set<string>>;
  mode: 'full' | 'custom';
  onToggle: (agentId: string, mcpId: string) => void;
  onSelectAllForAgent: (agentId: string) => void;
  onClearForAgent: (agentId: string) => void;
}) {
  if (agents.length === 0) return null;
  const readOnly = mode === 'full';
  return (
    <div className="mt-6 pt-5 border-t border-[var(--border-subtle)]">
      <div className="text-xs font-mono text-[var(--text-secondary)] mb-2 flex items-center gap-2">
        <Server className="w-3.5 h-3.5" />
        MCP servers
      </div>
      <div className="text-[11px] text-[var(--text-tertiary)] leading-relaxed mb-3">
        MCP URLs are <strong>not</strong> shipped by default — pick the ones to include.
        On import, each row goes straight into the recipient&apos;s <code>mcp_urls</code>
        with <code>connection_status</code> cleared so the local poller re-validates the URL.
      </div>
      <div className="space-y-4">
        {agents.map((a) => {
          const list = mcpsByAgent[a.agent_id];
          const selected = selectedByAgent[a.agent_id] || new Set<string>();
          return (
            <div key={a.agent_id}>
              <div className="flex items-center justify-between mb-1.5">
                <div className="font-mono text-xs text-[var(--text-secondary)]">
                  {a.name || a.agent_id}
                  <span className="text-[var(--text-tertiary)] ml-2">
                    {list ? `· ${list.length} MCP${list.length === 1 ? '' : 's'} · ${selected.size} selected` : '· loading…'}
                  </span>
                </div>
                {list && list.length > 0 && !readOnly && (
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => onSelectAllForAgent(a.agent_id)}
                      className="text-[10px] px-2 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)] font-mono"
                    >Select all</button>
                    <button
                      onClick={() => onClearForAgent(a.agent_id)}
                      className="text-[10px] px-2 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)] font-mono"
                    >Clear</button>
                  </div>
                )}
              </div>
              {list === null ? (
                <div className="flex items-center gap-2 text-[11px] text-[var(--text-tertiary)] py-2">
                  <Loader2 className="w-3 h-3 animate-spin" /> loading
                </div>
              ) : list.length === 0 ? (
                <div className="text-[11px] text-[var(--text-tertiary)] py-1.5">
                  No MCP URLs registered.
                </div>
              ) : (
                <div className="border border-[var(--border-subtle)]">
                  {list.map((m) => {
                    const checked = selected.has(m.mcp_id);
                    return (
                      <label
                        key={m.mcp_id}
                        className={cn(
                          'flex items-start gap-2 px-2.5 py-2 border-b border-[var(--border-subtle)] last:border-b-0 cursor-pointer hover:bg-[var(--bg-tertiary)]',
                          readOnly && 'cursor-default opacity-90'
                        )}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={readOnly}
                          onChange={() => !readOnly && onToggle(a.agent_id, m.mcp_id)}
                          className="mt-1"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-mono text-sm">{m.name || m.mcp_id}</span>
                            {!m.is_enabled && (
                              <span className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] text-[var(--text-tertiary)] font-mono">
                                disabled
                              </span>
                            )}
                            {m.connection_status && (
                              <span className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] text-[var(--text-tertiary)] font-mono">
                                {m.connection_status}
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] text-[var(--text-tertiary)] mt-0.5 font-mono break-all">
                            {m.url}
                          </div>
                          {m.description && (
                            <div className="text-[11px] text-[var(--text-secondary)] mt-0.5">
                              {m.description}
                            </div>
                          )}
                        </div>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {readOnly && (
        <div className="text-[11px] text-[var(--color-yellow-500)] flex items-center gap-1.5 mt-3">
          <AlertTriangle className="w-3 h-3" />
          Full snapshot mode auto-includes every MCP. Switch to Custom to pick.
        </div>
      )}
    </div>
  );
}


// ── ArtifactsTab — DB pointer rows ship per agent ─────────────────────────
// Underlying files always travel inside workspace.tar.gz (paths sit inside
// the agent's workspace), so unchecking an artifact here only drops the DB
// row from the bundle. On import the importer reapplies the recipient's
// `{aid}_{uid}/` prefix to file_path, forces session_id NULL + pinned=1.
function ArtifactsTab({
  agents, artifactsByAgent, selectedByAgent, mode, onToggle, onSelectAllForAgent, onClearForAgent,
}: {
  agents: any[];
  artifactsByAgent: Record<string, BundleArtifactPreview[] | null>;
  selectedByAgent: Record<string, Set<string>>;
  mode: 'full' | 'custom';
  onToggle: (agentId: string, artifactId: string) => void;
  onSelectAllForAgent: (agentId: string) => void;
  onClearForAgent: (agentId: string) => void;
}) {
  const readOnly = mode === 'full';
  if (agents.length === 0) {
    return <div className="text-xs text-[var(--text-tertiary)]">Pick at least one agent first.</div>;
  }
  const fmtSize = (n: number) => {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  };
  return (
    <div className="space-y-3">
      <div className="space-y-2 text-xs text-[var(--text-tertiary)] leading-relaxed border-l-2 border-[var(--accent-primary)]/40 pl-3">
        <p>
          <strong className="text-[var(--text-secondary)]">What you pick here:</strong> which
          artifact pointer rows ship in the bundle. Artifacts are the "this agent made
          an HTML page / chart / image / PDF" records you see in the chat preview
          cards.
        </p>
        <p>
          The <em>files themselves</em> always ride along inside
          <code>workspace.tar.gz</code> — this tab only controls whether the DB
          pointer row ships. Unchecking an artifact hides it from the recipient's
          Settings → Artifacts table but doesn't delete the underlying file. On
          import each shipped artifact is auto-pinned and its session_id cleared.
        </p>
      </div>
      <div className="space-y-4">
        {agents.map((a) => {
          const list = artifactsByAgent[a.agent_id];
          const selected = selectedByAgent[a.agent_id] || new Set<string>();
          return (
            <div key={a.agent_id}>
              <div className="flex items-center justify-between mb-1.5">
                <div className="font-mono text-xs text-[var(--text-secondary)]">
                  {a.name || a.agent_id}
                  <span className="text-[var(--text-tertiary)] ml-2">
                    {list ? `· ${list.length} artifact${list.length === 1 ? '' : 's'} · ${selected.size} selected` : '· loading…'}
                  </span>
                </div>
                {list && list.length > 0 && !readOnly && (
                  <div className="flex items-center gap-1">
                    <button
                      onClick={() => onSelectAllForAgent(a.agent_id)}
                      className="text-[10px] px-2 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)] font-mono"
                    >Select all</button>
                    <button
                      onClick={() => onClearForAgent(a.agent_id)}
                      className="text-[10px] px-2 py-0.5 border border-[var(--border-subtle)] hover:bg-[var(--bg-tertiary)] font-mono"
                    >Clear</button>
                  </div>
                )}
              </div>
              {list === null ? (
                <div className="flex items-center gap-2 text-[11px] text-[var(--text-tertiary)] py-2">
                  <Loader2 className="w-3 h-3 animate-spin" /> loading
                </div>
              ) : list.length === 0 ? (
                <div className="text-[11px] text-[var(--text-tertiary)] py-1.5">
                  This agent has no artifacts.
                </div>
              ) : (
                <div className="border border-[var(--border-subtle)]">
                  {list.map((art) => {
                    const checked = selected.has(art.artifact_id);
                    return (
                      <label
                        key={art.artifact_id}
                        className={cn(
                          'flex items-start gap-2 px-2.5 py-2 border-b border-[var(--border-subtle)] last:border-b-0 cursor-pointer hover:bg-[var(--bg-tertiary)]',
                          readOnly && 'cursor-default opacity-90'
                        )}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={readOnly}
                          onChange={() => !readOnly && onToggle(a.agent_id, art.artifact_id)}
                          className="mt-1"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-mono text-sm truncate">{art.title || art.artifact_id}</span>
                            <span className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] text-[var(--text-tertiary)] font-mono">
                              {art.kind}
                            </span>
                            <span className="text-[10px] text-[var(--text-tertiary)] font-mono">
                              {fmtSize(art.size_bytes)}
                            </span>
                            {art.pinned && (
                              <span className="text-[10px] px-1.5 py-0.5 border border-[var(--border-subtle)] text-[var(--text-tertiary)] font-mono">
                                pinned
                              </span>
                            )}
                          </div>
                          {art.file_path && (
                            <div className="text-[11px] text-[var(--text-tertiary)] mt-0.5 font-mono break-all">
                              {art.file_path}
                            </div>
                          )}
                        </div>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {readOnly && (
        <div className="text-[11px] text-[var(--color-yellow-500)] flex items-center gap-1.5 mt-3">
          <AlertTriangle className="w-3 h-3" />
          Full snapshot mode auto-includes every artifact. Switch to Custom to pick.
        </div>
      )}
    </div>
  );
}
