/**
 * useTimezoneSync - Hook for syncing browser timezone with backend
 *
 * Automatically detects the user's browser timezone and syncs it to the backend
 * when the user is logged in. This ensures that time-related features (like Job
 * scheduling and notifications) display times in the user's local timezone.
 */

import { useEffect, useRef } from 'react';
import { api } from '@/lib/api';
import { useConfigStore } from '@/stores';

/**
 * Hook that syncs the user's browser timezone to the backend.
 *
 * Should be called in the App component so timezone is synced on page load.
 * Only syncs when:
 * - User is logged in
 * - Timezone hasn't been synced in this session yet
 */
export function useTimezoneSync(): void {
  const { userId, isLoggedIn } = useConfigStore();
  const hasSynced = useRef(false);

  useEffect(() => {
    // Only sync when logged in and haven't synced yet in this session
    if (!isLoggedIn || !userId || hasSynced.current) {
      return;
    }

    const syncTimezone = async () => {
      try {
        // Get browser timezone using Intl API
        const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;

        // Sync to backend
        const result = await api.updateTimezone(userId, timezone);

        if (result.success) {
          hasSynced.current = true;
        } else {
          console.warn('Failed to sync timezone:', result.error);
        }
      } catch (error) {
        // Silently fail - timezone sync is not critical
        console.warn('Error syncing timezone:', error);
      }
    };

    syncTimezone();
  }, [isLoggedIn, userId]);
}
