/**
 * segmentTurn — split one turn's event sequence at each user-facing
 * fragment.
 *
 * A turn naturally looks like [think, tool, tool, reply1, think, tool,
 * reply2, ...]. The bubble shows the reply, and the process before each
 * reply belongs to it — so cutting at every user-facing fragment is all
 * the segmentation there is.
 *
 * One function serves three paths — the live currentEvents, the
 * just-finished message.timeline, and the reloaded /event-log timeline
 * (normalised by timelineToEvents) — so the live view and the
 * post-refresh view agree by construction: one implementation, not two
 * that happen to match.
 */
import type {
  EventLogTimelineEntry,
  ProcessEvent,
  Segment,
  SegmentReply,
  TurnEvent,
} from '@/types';
import { isOwnerReplyTool } from '@/lib/ownerTools';

const isProcess = (e: TurnEvent): e is ProcessEvent =>
  e.type === 'thinking' || e.type === 'tool_call' || e.type === 'tool_output';

export function segmentTurn(events: TurnEvent[]): Segment[] {
  const segments: Segment[] = [];
  let process: ProcessEvent[] = [];

  const push = (reply: SegmentReply | null) => {
    segments.push({ process, reply });
    process = [];
  };

  for (const event of events) {
    if (isProcess(event)) {
      process.push(event);
      continue;
    }
    // A plan belongs to no segment: it answers "where are we now" and
    // renders separately in the pinned PlanStrip above the composer.
    if (event.type === 'plan') continue;

    if (event.type === 'reply') {
      push({ content: event.content, via: event.reply_via, streaming: event.streaming });
      continue;
    }

    // native_output: consecutive native text is one utterance split
    // into deltas — merge it into the previous segment. A tool call in
    // between means the agent spoke again: start a new segment.
    const last = segments[segments.length - 1];
    const mergeable =
      !!last && !!last.reply && last.reply.via === undefined && process.length === 0;
    if (mergeable && last.reply) {
      last.reply = { ...last.reply, content: last.reply.content + event.content };
    } else {
      push({ content: event.content });
    }
  }

  // Trailing process attaches to the last segment — "kept working after
  // the last sentence" is exactly the thing worth seeing. A turn with no
  // reply at all yields one reply=null segment so no process is lost.
  if (process.length > 0) {
    const last = segments[segments.length - 1];
    if (last) last.process = [...last.process, ...process];
    else push(null);
  }
  return segments;
}

/**
 * /event-log timeline → TurnEvent[].
 *
 * This logic used to live inline in MessageBubble and deliberately
 * DROPPED 'reply' (the reply text was already rendered once from
 * message.content). Segmentation needs the reply as its cut point, so
 * it is kept here — the double-render is now prevented by the bubble
 * rendering segment.reply only.
 */
export function timelineToEvents(
  timeline: EventLogTimelineEntry[],
  { convertOwnerReplyTool = true }: { convertOwnerReplyTool?: boolean } = {},
): TurnEvent[] {
  const events: TurnEvent[] = [];
  // Stored tool_output entries often carry no tool_name; in a time-ordered
  // log an output belongs to the nearest preceding call, so carry that name
  // forward — a literal placeholder next to every output row reads as a bug.
  // Older backends stamped the placeholder "unknown" at the API layer;
  // treat it as missing so it, too, inherits the real name.
  const realName = (name: string | undefined): string =>
    name && name !== 'unknown' ? name : '';
  let lastToolName = '';
  timeline.forEach((entry, idx) => {
    const id = `tl-${idx}`;
    const ts = idx;
    switch (entry.type) {
      case 'thinking':
        if (entry.content) {
          // The tier was resolved backend-side (chat_history splits blocks on
          // a tier switch, so an entry is one tier), and carrying it here is
          // what keeps a reloaded turn looking like the live one.
          events.push({
            id, ts, type: 'thinking',
            content: entry.content,
            monologue: !!entry.monologue,
          });
        }
        break;
      case 'tool_call': {
        const toolName = realName(entry.tool_name);
        lastToolName = toolName;
        // The persisted timeline has no type='reply': a reply is stored
        // as the send_message tool call itself (tool_input.content is the
        // reply text). The live path (chatStore) performs this same
        // tool_call→reply conversion; matching it here is what makes a
        // reloaded turn segmentable at all. The acknowledgement
        // tool_output stays a process event — same as the live path.
        // convertOwnerReplyTool=false keeps reply-tool calls as process
        // rows — the collapsed bubble's disclosure shows send_message like
        // any other tool. This divergence between the two consumers is a
        // design choice, expressed as an option so there is exactly one
        // implementation of this conversion.
        if (convertOwnerReplyTool && isOwnerReplyTool(toolName)) {
          const content = (entry.tool_input as Record<string, unknown> | undefined)?.content;
          if (typeof content === 'string' && content) {
            events.push({ id, ts, type: 'reply', content, reply_via: entry.reply_via });
          }
          break;
        }
        events.push({
          id, ts, type: 'tool_call',
          tool_name: toolName,
          tool_input: entry.tool_input || {},
          reply_via: entry.reply_via,
        });
        break;
      }
      case 'tool_output':
        events.push({
          id, ts, type: 'tool_output',
          tool_name: realName(entry.tool_name) || lastToolName,
          output: entry.tool_output || '',
        });
        break;
      case 'native_output':
        if (entry.content) events.push({ id, ts, type: 'native_output', content: entry.content });
        break;
      case 'reply':
        if (entry.content) {
          events.push({ id, ts, type: 'reply', content: entry.content, reply_via: entry.reply_via });
        }
        break;
    }
  });
  return events;
}
