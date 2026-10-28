/**
 * @file NetmindActionZone.tsx
 * @author NetMind.AI
 * @date 2026-07-10
 * @description The plan × runway action zone of the Account & Subscription
 * panel: at most ONE promoted spend action, everything else demoted.
 *
 *   pro_cancelled            → Resume auto-renew + "Add credits" modal
 *   pro_onetime              → Renew (buy N more months) + "Add credits";
 *                              NEVER cancel/resume — see the branch below
 *   free × low               → Upgrade-to-Nexus-Pro card inline; top-up behind a link
 *   pro_active × low         → top-up promoted directly (already Pro — no upsell)
 *   free × healthy           → "Upgrade or top up" opens a MODAL (Pro card +
 *                              "just top up" link) — never two peer spend buttons
 *   pro_active × healthy     → member-pricing note + "Top up or manage
 *                              subscription" modal (cancel + top-up)
 *
 * The healthy-state manage action is a Dialog (mirrors LLM Providers' add-provider
 * modal), NOT an inline disclosure, and never offers "subscribe vs recharge" as a
 * choice: the Pro card leads, top-up is a demoted line. Owns its own open/reveal
 * state (pure UI toggles); the panel just feeds data + guarded handlers.
 *
 * "used up" copy is only used when the free tier is KNOWN exhausted; otherwise the
 * neutral "low on credits" copy. Purely presentational — money handlers stay in
 * NetmindAccountPanel.
 */

import { type ReactNode, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { platform } from '@/lib/platform';
import { Button, Dialog, DialogContent } from '@/components/ui';
import { NetmindUpsellCard } from './NetmindUpsellCard';
import type { Runway } from './netmindRunway';
import type { SubscriptionPlan } from '@/types';

// NarraNexus pricing page (plans + model pricing in product terms; replaced the
// raw NetMind pricing page 2026-07-18; moved off the `website.` subdomain to
// the apex 2026-08-19) — the "learn more" depth that doesn't belong here.
//
// Build-time constant, NOT deploy config: changing it needs a frontend rebuild.
// If this URL starts moving (a relaunch, per-language pricing pages), promote it
// to window.__NARRANEXUS_CONFIG__ alongside mode/apiUrl instead of editing here
// again — that is the mechanism that already exists for values a deploy sets.
const PRICING_URL = 'https://narra.nexus/pricing';

interface NetmindActionZoneProps {
  state: 'free' | 'pro_active' | 'pro_cancelled' | 'pro_onetime';
  runway: Runway;
  /** True only when the free tier is KNOWN exhausted (pct === 0). */
  freeTierExhausted: boolean;
  busy: boolean;
  polling: boolean;
  proPlan: SubscriptionPlan | null;
  /** Top-up controls element (state + guards owned by the panel). */
  topUp: ReactNode;
  /** "How do you want to pay for Pro" — rendered for a free user starting one
   *  AND for a one-time subscriber extending theirs. */
  proPurchase?: ReactNode;
  /** Open the purchase dialog immediately — `/pay` redirects here with
   *  `intent=buy`, and that CTA must land ON the purchase, not beside it. */
  openBuyOnMount?: boolean;
  onCancel: () => void;
  onReactivate: () => void;
}

export function NetmindActionZone({
  state,
  runway,
  freeTierExhausted,
  busy,
  polling,
  proPlan,
  topUp,
  proPurchase,
  openBuyOnMount = false,
  onCancel,
  onReactivate,
}: NetmindActionZoneProps) {
  const { t } = useTranslation();
  const [manageOpen, setManageOpen] = useState(false);
  const [showTopUp, setShowTopUp] = useState(false);
  const [buyOpen, setBuyOpen] = useState(openBuyOnMount);

  const closeManage = () => {
    setManageOpen(false);
    setShowTopUp(false); // reset the in-modal top-up reveal on close
  };

  const openPricing = () => void platform.openExternal(PRICING_URL);
  const pricingLink = (
    <div className="flex justify-end">
      <button
        type="button"
        onClick={openPricing}
        className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline underline-offset-2"
      >
        {t('settings.netmind.pricingLink', 'See all models & pricing')} ↗
      </button>
    </div>
  );

  // A real outline button, not a 12px text link: this is the panel's payment
  // entry, and users coming from the pricing page couldn't find it (P2 bug,
  // 2026-07-31). Outline, not accent — it stays subordinate to any promoted
  // CTA next to it (e.g. Resume auto-renew), preserving the
  // one-promoted-spend-action rule. whitespace-nowrap: Button's sm size is a
  // fixed h-8, so a wrapped label would overflow the border.
  const manageTrigger = (label: string) => (
    <div className="flex justify-end">
      <Button
        variant="outline"
        size="sm"
        className="whitespace-nowrap"
        onClick={() => setManageOpen(true)}
      >
        {label} ›
      </Button>
    </div>
  );

  // "just top up instead" — a demoted line that reveals the top-up controls in
  // place. Shared by the free×low inline view and the free manage modal.
  const topUpDisclosure = (
    <>
      <div className="text-xs text-[var(--text-tertiary)]">
        <button
          type="button"
          onClick={() => setShowTopUp((v) => !v)}
          aria-expanded={showTopUp}
          className="text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline underline-offset-2"
        >
          {t('settings.netmind.topupOrLink', 'Just need a one-time top-up? Add credits')} ›
        </button>
      </div>
      {showTopUp && topUp}
    </>
  );

  // The plan-forward content: Pro card leads, top-up is a demoted line. NO
  // "subscribe vs recharge" peer choice. Used inline (free×low) and in the
  // free manage modal.
  // The upsell CTA opens the rail choice; it does NOT subscribe by card. Going
  // straight to card is what made "buy Pro with Alipay" unreachable for anyone
  // who was not already a one-time subscriber — including a one-time subscriber
  // whose period had lapsed, who then could never buy it back.
  const planBlock = (
    <div className="space-y-3">
      <NetmindUpsellCard
        proPlan={proPlan}
        // closeManage first: on the healthy-free path this CTA lives INSIDE the
        // manage dialog, and Dialog has no focus trap while each instance binds
        // its own document-level Escape — two stacked scrims, and one Escape
        // closing both would drop the user out of a purchase mid-way.
        onUpgrade={() => {
          closeManage();
          setBuyOpen(true);
        }}
        busy={busy || polling}
      />
      {topUpDisclosure}
      {pricingLink}
    </div>
  );

  const buyDialog = (
    <Dialog
      isOpen={buyOpen}
      onClose={() => setBuyOpen(false)}
      title={t('settings.netmind.buyProTitle', 'Upgrade to Nexus Pro')}
      size="lg"
    >
      <DialogContent className="space-y-4">
        {proPurchase}
        {pricingLink}
      </DialogContent>
    </Dialog>
  );

  // ── pro_onetime: a purchase that ENDS, so renewing is the whole job ────────
  // Neither cancel nor resume appears here, and that is not tidiness:
  //   - `reactivate` does not apply to a purchase that never renews;
  //   - `cancel` returns 200 {"status":"auto_renew_off"} on one of these
  //     (measured on dev 2026-08-19) — a silent no-op that would let the UI
  //     announce a cancellation that did not happen and was never needed.
  // The one action that keeps this user Pro is buying more months, so it is
  // the promoted one regardless of runway: unlike a card subscriber, nothing
  // will renew for them if they ignore it.
  if (state === 'pro_onetime') {
    return (
      <>
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            {/* Member pricing, NOT "Pro is active — one-time purchase": the
                plan row directly above already states the plan, the end date
                and that it does not renew. Repeating it here spent the most
                prominent line in the zone on something the eye had just read.
                This says what being Pro BUYS them, which is the same benefit
                pro_active states in this exact position. */}
            <div className="flex items-center gap-1.5 text-sm text-[var(--color-success)]">
              <span aria-hidden>✦</span>
              <span>
                {t('settings.netmind.proMemberActive', 'Member pricing active — up to 50% off')}
              </span>
            </div>
            <Button variant="accent" size="sm" onClick={() => setManageOpen(true)}>
              {t('settings.netmind.renewBtn', 'Renew')}
            </Button>
          </div>
          {pricingLink}
        </div>
        <Dialog
          isOpen={manageOpen}
          onClose={closeManage}
          title={t('settings.netmind.renewTitle', 'Keep Pro going')}
          size="lg"
        >
          <DialogContent className="space-y-4">
            {proPurchase}
            <hr className="border-[var(--nm-hairline)]" />
            {topUp}
          </DialogContent>
        </Dialog>
      </>
    );
  }

  // ── pro_cancelled: resume (runway-agnostic) + manage-balance modal ─────────
  if (state === 'pro_cancelled') {
    return (
      <>
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-[var(--text-secondary)]">
              {t('settings.netmind.proMemberActive', 'Member pricing active — up to 50% off')}
            </p>
            <Button variant="accent" size="sm" onClick={onReactivate} disabled={busy}>
              {busy
                ? t('settings.netmind.working', 'Working…')
                : t('settings.netmind.reactivateBtn', 'Resume auto-renew')}
            </Button>
          </div>
          {manageTrigger(t('settings.netmind.manageBalance', 'Add credits'))}
        </div>
        <Dialog
          isOpen={manageOpen}
          onClose={closeManage}
          title={t('settings.netmind.manageBalance', 'Add credits')}
          size="lg"
        >
          <DialogContent className="space-y-4">
            {topUp}
            {pricingLink}
          </DialogContent>
        </Dialog>
      </>
    );
  }

  // ── low (inline, urgent) ───────────────────────────────────────────────────
  if (runway === 'low') {
    if (state === 'free') {
      return (
        <div className="space-y-3">
          <p className="text-sm font-medium text-[var(--color-warning)]">
            {freeTierExhausted
              ? t('settings.netmind.exhaustedChoose', 'Free tier used up. To keep going:')
              : t('settings.netmind.lowChoose', "You're low on credits. To keep going:")}
          </p>
          {planBlock}
          {buyDialog}
        </div>
      );
    }
    // pro_active × low → top-up is the action (already Pro, no upsell)
    return (
      <div className="space-y-3">
        <p className="text-sm font-medium text-[var(--color-warning)]">
          {t('settings.netmind.needTopup',
            'Your grant and balance are running low. Add credits to keep going:')}
        </p>
        {topUp}
        {pricingLink}
      </div>
    );
  }

  // ── healthy free: calm; manage opens a modal (Pro-forward, top-up demoted) ──
  if (state === 'free') {
    return (
      <>
        {manageTrigger(t('settings.netmind.managePlan', 'Upgrade or top up'))}
        <Dialog
          isOpen={manageOpen}
          onClose={closeManage}
          title={t('settings.netmind.managePlan', 'Upgrade or top up')}
          size="lg"
        >
          <DialogContent>{planBlock}</DialogContent>
        </Dialog>
        {buyDialog}
      </>
    );
  }

  // ── healthy pro_active: member note + manage modal (cancel + top-up) ────────
  return (
    <>
      <div className="space-y-3">
        <div className="flex items-center gap-1.5 text-sm text-[var(--color-success)]">
          <span aria-hidden>✦</span>
          <span>{t('settings.netmind.proMemberActive', 'Member pricing active — up to 50% off')}</span>
        </div>
        {manageTrigger(t('settings.netmind.manageSubscription', 'Top up or manage subscription'))}
      </div>
      <Dialog
        isOpen={manageOpen}
        onClose={closeManage}
        title={t('settings.netmind.manageSubscription', 'Top up or manage subscription')}
        size="lg"
      >
        <DialogContent className="space-y-4">
          {/* The plan intro in its subscribed state — the user sees what
              their Pro includes right where they'd cancel it. */}
          <NetmindUpsellCard proPlan={proPlan} busy={busy} subscribed />
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-[var(--text-secondary)]">
              {t('settings.netmind.cancelBtn', 'Cancel subscription')}
            </p>
            <Button variant="outline" size="sm" onClick={onCancel} disabled={busy}>
              {busy
                ? t('settings.netmind.working', 'Working…')
                : t('settings.netmind.cancelBtn', 'Cancel subscription')}
            </Button>
          </div>
          {topUp}
          {pricingLink}
        </DialogContent>
      </Dialog>
    </>
  );
}
