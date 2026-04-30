/**
 * @file_name: DashboardSkeleton.tsx
 * @date: 2026-04-13
 * @description: v2.2 G1 — Suspense fallback for the lazy /app/dashboard chunk.
 * Mounted by MainLayout's inner <Suspense> so the Sidebar stays visible while
 * the chunk downloads. Shape mirrors the real dashboard grid so the swap is
 * not a layout shift.
 */
export function DashboardSkeleton() {
  return (
    <div data-testid="dashboard-skeleton" className="p-6 space-y-4 animate-pulse">
      <div className="h-7 w-48 rounded bg-[var(--bg-elevated)]" />
      <div className="h-10 w-full rounded-lg bg-[var(--bg-elevated)]" />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div
            key={i}
            className="h-28 rounded-2xl border border-[var(--border-default)] bg-[var(--bg-elevated)]"
          />
        ))}
      </div>
    </div>
  );
}

export default DashboardSkeleton;
