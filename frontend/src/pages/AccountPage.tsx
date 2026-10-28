/**
 * @file_name: AccountPage.tsx
 * @author:
 * @date: 2026-08-06
 * @description: Legacy route alias. The account / billing / subscription
 * surface lives INSIDE Settings (the ?tab=account pane), so the left nav
 * stays visible and Account is switchable like every other pane. This
 * route survives only for old links and bookmarks; it forwards with the
 * whole query preserved so Stripe's post-payment return parameters keep
 * working wherever they land first.
 */
import { Navigate, useSearchParams } from 'react-router-dom';

export default function AccountPage() {
  const [searchParams] = useSearchParams();
  const params = new URLSearchParams(searchParams);
  params.set('tab', 'account');
  return <Navigate to={`/app/settings?${params.toString()}`} replace />;
}
