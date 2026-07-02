/**
 * WeChatConfig — Per-agent personal-WeChat (iLink) binding configuration.
 *
 * States:
 *   1. No account bound → "Connect" button → QR-scan flow:
 *        start → render login QR → poll until the user scans + confirms.
 *   2. Account bound    → connected status (owner / base url) + Unbind.
 *
 * Unlike the bot channels (Telegram / Slack / Lark) there is NO token to
 * paste — personal WeChat authenticates by scanning a login QR with the
 * phone, exactly like WeChat web. The bind is therefore two server calls:
 * `/qrcode/start` (get the QR) then a poll loop on `/qrcode/poll` that the
 * gateway long-polls; on "confirmed" the backend has already persisted the
 * iLink bot_token, so the UI just re-fetches the (sanitised) credential.
 *
 * The owner's wxid is opaque until the first inbound DM (the gateway never
 * reveals it at bind time), so right after binding the owner shows as
 * "pending" until the owner messages the account once — same first-contact
 * pattern as Telegram's @username trust signal.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation, Trans } from 'react-i18next';
import {
  MessageSquare,
  Link,
  Unlink,
  Loader2,
  CheckCircle,
  AlertCircle,
  RefreshCw,
  ExternalLink,
  XCircle,
} from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';

import { Card, CardHeader, CardTitle, CardContent, Button, useConfirm } from '@/components/ui';
import { useConfigStore } from '@/stores';
import { api } from '@/lib/api';
import type { WeChatCredentialData } from '@/types';

import type { ChannelConfigProps } from './IMChannelsSection';

export function WeChatConfig({ onBindStateChange }: ChannelConfigProps = {}) {
  const { t } = useTranslation();
  const { agentId } = useConfigStore();

  const [credential, setCredential] = useState<WeChatCredentialData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [unbindLoading, setUnbindLoading] = useState(false);

  // QR-scan flow state.
  const [connecting, setConnecting] = useState(false);   // start request in flight
  const [qrUrl, setQrUrl] = useState('');                // scannable WeChat URL (encoded into a QR inline)
  const [polling, setPolling] = useState(false);         // poll loop is active

  const mountedRef = useRef(true);
  // Guards the recursive poll loop — flipping to false stops it (cancel/unmount).
  const pollingActiveRef = useRef(false);
  const { confirm, dialog: confirmDialog } = useConfirm();

  const fetchCredential = useCallback(async () => {
    if (!agentId) return;
    try {
      setLoading(true);
      setError('');
      const res = await api.getWeChatCredential(agentId);
      if (!mountedRef.current) return;
      if (res.success) {
        setCredential(res.data || null);
      } else {
        setError(res.error || t('awareness.wechat.errLoad'));
      }
    } catch (e: unknown) {
      if (!mountedRef.current) return;
      setError(e instanceof Error ? e.message : t('awareness.wechat.errFetch'));
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [agentId, t]);

  const stopPolling = useCallback(() => {
    pollingActiveRef.current = false;
    if (mountedRef.current) {
      setPolling(false);
      setQrUrl('');
    }
  }, []);

  useEffect(() => {
    setError('');
    setCredential(null);
    stopPolling();
    fetchCredential();
    // Stop any in-flight poll when the selected agent changes.
    return () => stopPolling();
  }, [fetchCredential, stopPolling]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      pollingActiveRef.current = false;
    };
  }, []);

  // Recursive poll: /qrcode/poll long-polls on the gateway side (~30s), so we
  // re-invoke immediately on "wait" — the server call IS the pacing. We never
  // tight-loop the client.
  const runPollLoop = useCallback(
    async (qrcode: string) => {
      while (pollingActiveRef.current) {
        let res;
        try {
          res = await api.pollWeChatQrcode(agentId, qrcode);
        } catch (e: unknown) {
          if (!pollingActiveRef.current) return;
          setError(e instanceof Error ? e.message : t('awareness.wechat.errPoll'));
          stopPolling();
          return;
        }
        if (!pollingActiveRef.current) return;
        if (!res.success) {
          setError(res.error || t('awareness.wechat.errPoll'));
          stopPolling();
          return;
        }
        if (res.data?.status === 'confirmed') {
          stopPolling();
          await fetchCredential();
          onBindStateChange?.();
          return;
        }
        // status === "wait" — long-poll expired with no scan; re-poll.
      }
    },
    [agentId, fetchCredential, onBindStateChange, stopPolling, t],
  );

  const handleConnect = async () => {
    if (!agentId) return;
    setConnecting(true);
    setError('');
    try {
      const res = await api.startWeChatQrcode(agentId);
      if (!mountedRef.current) return;
      if (!res.success || !res.data?.qrcode || !res.data?.qr_url) {
        setError(res.error || t('awareness.wechat.errNoQr'));
        return;
      }
      setQrUrl(res.data.qr_url);
      setPolling(true);
      pollingActiveRef.current = true;
      // Fire-and-forget the loop; it self-guards on pollingActiveRef.
      void runPollLoop(res.data.qrcode);
    } catch (e: unknown) {
      if (mountedRef.current) {
        setError(e instanceof Error ? e.message : t('awareness.wechat.errStart'));
      }
    } finally {
      if (mountedRef.current) setConnecting(false);
    }
  };

  const handleUnbind = async () => {
    if (!agentId) return;
    const ok = await confirm({
      title: t('awareness.wechat.disconnectConfirmTitle'),
      message: t('awareness.wechat.disconnectConfirmMessage'),
      confirmText: t('awareness.wechat.disconnect'),
      danger: true,
    });
    if (!ok) return;
    setUnbindLoading(true);
    setError('');
    try {
      const res = await api.unbindWeChat(agentId);
      if (res.success) {
        await fetchCredential();
        onBindStateChange?.();
      } else {
        setError(res.error || t('awareness.wechat.errDisconnect'));
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t('awareness.wechat.errDisconnect'));
    } finally {
      if (mountedRef.current) setUnbindLoading(false);
    }
  };

  return (
    <Card>
      {confirmDialog}
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageSquare className="w-4 h-4" />
          WeChat
        </CardTitle>
        <button
          onClick={() => fetchCredential()}
          disabled={loading}
          className="p-1 rounded hover:bg-[var(--bg-tertiary)] transition-colors text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
          title={t('awareness.common.refresh')}
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && (
          <div role="alert" className="flex items-center gap-2 text-sm text-[var(--color-red-500)] border border-[var(--color-red-500)] p-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
            {error}
          </div>
        )}

        {/* State 1: No account bound */}
        {!credential && !polling && (
          <div className="space-y-3">
            <p className="text-xs text-[var(--text-secondary)]">
              {t('awareness.wechat.intro')}
            </p>
            <div className="text-xs text-[var(--color-yellow-500)]" role="note">
              {t('awareness.wechat.warning')}
            </div>
            <Button
              onClick={handleConnect}
              disabled={connecting}
              className="w-full"
              size="sm"
            >
              {connecting ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Link className="w-4 h-4 mr-2" />}
              {t('awareness.wechat.connect')}
            </Button>
          </div>
        )}

        {/* State 1b: QR shown, waiting for scan */}
        {!credential && polling && (
          <div className="space-y-3">
            <p className="text-xs text-[var(--text-secondary)]">
              <Trans i18nKey="awareness.wechat.scanInstruction">
                Open WeChat on your phone → <strong>Discover → Scan</strong>, and
                scan this code. Keep this panel open until it confirms.
              </Trans>
            </p>
            <div className="flex flex-col items-center gap-2 border border-[var(--border-default)] rounded p-4">
              {qrUrl && (
                // The gateway's qr_url is a WeChat short URL, not an image — we
                // encode it into a QR inline (white quiet-zone padding so phone
                // cameras lock on). The liteapp page below is a fallback.
                <div className="bg-white p-3 rounded">
                  <QRCodeSVG value={qrUrl} size={176} level="M" />
                </div>
              )}
              <span className="flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t('awareness.wechat.waitingForScan')}
              </span>
              <a
                href={qrUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-[var(--text-secondary)] hover:text-[var(--accent-primary)] hover:underline inline-flex items-center gap-1"
              >
                {t('awareness.wechat.cantScan')} <ExternalLink className="w-3 h-3" />
              </a>
            </div>
            <Button
              onClick={stopPolling}
              variant="outline"
              size="sm"
              className="w-full"
            >
              <XCircle className="w-4 h-4 mr-2" />
              {t('awareness.common.cancel')}
            </Button>
          </div>
        )}

        {/* State 2: Account bound */}
        {credential && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm">
                <span className="text-[var(--text-primary)] font-medium">
                  {credential.owner_name || credential.bot_wx_id || t('awareness.wechat.accountFallback')}
                </span>
              </div>
              <span className="flex items-center gap-1 text-xs text-[var(--color-green-500)]">
                <CheckCircle className="w-3 h-3" aria-hidden="true" /> {t('awareness.common.connected')}
              </span>
            </div>
            {credential.owner_wx_id ? (
              <div className="text-xs text-[var(--text-secondary)]">
                {t('awareness.common.owner')}: <span className="text-[var(--text-primary)]">{credential.owner_name || credential.owner_wx_id}</span>
              </div>
            ) : (
              <div className="text-xs text-[var(--color-yellow-500)]" role="note">
                {t('awareness.wechat.ownerPending')}
                <div className="mt-1 text-[var(--text-secondary)]">
                  {t('awareness.wechat.ownerPendingNote')}
                </div>
              </div>
            )}
            <div className="flex gap-2">
              <Button
                onClick={handleUnbind}
                disabled={unbindLoading}
                variant="outline"
                size="sm"
                className="flex-1"
              >
                {unbindLoading ? (
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                ) : (
                  <Unlink className="w-4 h-4 mr-2" />
                )}
                {t('awareness.wechat.disconnect')}
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
