/**
 * modelBrandIcons — protocol/model-id → real brand icon matching.
 *
 * Split out of components/icons/ModelBrandIcons.tsx because react-refresh
 * forbids mixing component exports with plain function exports in one file;
 * the icon components themselves live there, this is pure matching logic.
 */
import {
  ClaudeBrandIcon,
  DeepSeekBrandIcon,
  GeminiBrandIcon,
  KimiBrandIcon,
  MiniMaxBrandIcon,
  OpenAIBrandIcon,
  QwenBrandIcon,
  ZhipuBrandIcon,
  type ProtocolIconComponent,
} from '@/components/icons/ModelBrandIcons';

/** Every Provider.protocol in this app is 'anthropic' | 'openai' — both map
 *  1:1 to the Claude / OpenAI mark, since every anthropic-protocol provider
 *  here is realistically a Claude-model card. */
export function getProtocolBrandIcon(protocol: string | undefined): ProtocolIconComponent | null {
  if (protocol === 'anthropic') return ClaudeBrandIcon;
  if (protocol === 'openai') return OpenAIBrandIcon;
  return null;
}

/** Best-effort vendor detection from a model id string (case-insensitive
 *  substring match) — covers every group in agentFramework.ts's
 *  MODEL_SUGGESTION_GROUPS. A custom base_url provider can carry an
 *  arbitrary model id this can't place; callers fall back to a generic icon
 *  when this returns null. */
/** Which brand marks are drawn black-on-transparent and need `dark:invert`
 *  in dark mode. The ONE list — every page that renders a brand mark asks
 *  here instead of comparing against a specific icon component, so a new
 *  black-on-transparent mark is one entry rather than one edit per page. */
const INVERTS_IN_DARK: ReadonlySet<unknown> = new Set([OpenAIBrandIcon]);

export function iconInvertsInDark(icon: unknown): boolean {
  return INVERTS_IN_DARK.has(icon);
}

export function getModelBrandIcon(modelId: string | undefined): ProtocolIconComponent | null {
  if (!modelId) return null;
  const id = modelId.toLowerCase();
  if (id.includes('claude')) return ClaudeBrandIcon;
  if (id.includes('gpt') || /^o[0-9]/.test(id)) return OpenAIBrandIcon;
  if (id.includes('gemini')) return GeminiBrandIcon;
  if (id.includes('glm')) return ZhipuBrandIcon;
  if (id.includes('kimi')) return KimiBrandIcon;
  if (id.includes('qwen')) return QwenBrandIcon;
  if (id.includes('minimax')) return MiniMaxBrandIcon;
  if (id.includes('deepseek')) return DeepSeekBrandIcon;
  return null;
}
