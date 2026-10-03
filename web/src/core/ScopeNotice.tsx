import { useLocation } from 'react-router-dom';
import { Info } from 'lucide-react';
import { isFleetWidePath } from './nav';
import { useConsignor } from '../modules/analytics/context/ConsignorContext';
import { useTripClass } from '../modules/analytics/context/TripClassContext';

/**
 * Says so when the sidebar's consignor or trip-class filter is set but the
 * page on screen does not apply it (the fence, live and data-quality views
 * read the whole fleet). Without this, a filtered reader would take a
 * fleet-wide number for a filtered one.
 */
export default function ScopeNotice() {
  const { pathname } = useLocation();
  const { consignorId, consignor } = useConsignor();
  const { tripClass } = useTripClass();
  if (!isFleetWidePath(pathname) || (consignorId == null && !tripClass)) return null;

  const filters = [
    consignorId != null ? `consignor ${consignor?.name ?? consignorId}` : null,
    tripClass ? `${tripClass} trips` : null,
  ].filter(Boolean).join(' and ');

  return (
    <div className="mb-4 flex items-start gap-2 rounded-lg border border-amber-900/60 bg-amber-950/20 px-3 py-2 text-sm text-amber-200">
      <Info className="w-4 h-4 mt-0.5 shrink-0" />
      <span>This view covers the whole fleet: the {filters} filter does not apply here.</span>
    </div>
  );
}
