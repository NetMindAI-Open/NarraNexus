/**
 * @file_name: MigrationGuide.tsx
 * @author: NetMind.AI
 * @date: 2026-07-30
 * @description: The "import lives behind the + button" coach-mark — shown only
 * to users who SKIPPED the import step of the first-run flow.
 *
 * 2026-08-27 (Owner decision): this component used to detect other-framework
 * agents and pop its own welcome dialog over the chat. That offer is now step 2
 * of [[WelcomePage]], which arms this coach-mark when the user declines it. So
 * nothing here detects or fetches any more: it is a gate around
 * [[MigrationCoachmark]], and the flow is the only thing that arms it.
 *
 * LOCAL ONLY: import reads the user's filesystem, so pointing at it on cloud
 * would advertise a feature that 503s there.
 */

import { useState } from 'react';
import { useRuntimeStore, useConfigStore } from '@/stores';
import { readMigrationGuide, writeMigrationGuide } from '@/lib/migrationGuide';
import { MigrationCoachmark } from './MigrationCoachmark';

/** Gate: local + logged-in. Keying the inner on userId means its lazy state
 *  initializer reads the CORRECT user's persisted state exactly once. */
export function MigrationGuide() {
  const isLocal = useRuntimeStore((s) => s.mode) === 'local';
  const userId = useConfigStore((s) => s.userId);
  if (!isLocal || !userId) return null;
  return <MigrationGuideInner key={userId} userId={userId} />;
}

function MigrationGuideInner({ userId }: { userId: string }) {
  const [state, setState] = useState(() => readMigrationGuide(userId));
  if (!state.coachmarkPending || state.coachmarkDone) return null;
  return (
    <MigrationCoachmark
      onDismiss={() => setState(writeMigrationGuide(userId, { coachmarkDone: true }))}
    />
  );
}
