import { Check, X, MapPinOff, ShieldCheck, Info } from 'lucide-react';
import { useApi } from '../../hooks/useApi';
import { getTripDeliveryGeofence } from '../../services/geofence';
import Spinner from '../ui/Spinner';

/**
 * The consignor's complaint, evaluated against this one trip.
 *
 * They said: *"Points where GPS data being captured. Customer was not geo
 * fenced, met expected km running ~90% but trip closed as 'No geo fenced
 * delivery locations'."*
 *
 * That is four claims, not one, so it is shown as four checks rather than a
 * single badge. A trip that runs the distance with healthy GPS and still closes
 * without a geofence is a gap in eTrans's geofence master — the consignor's
 * point, and not a transport failure. A trip that fails a check is shown
 * failing it, because a partial match presented as the whole complaint is how
 * a real finding gets discredited.
 *
 * The "~90%" baseline is the **median distance actually run on this lane**.
 * There is no planned-km field anywhere in the feed, so the panel says that
 * plainly instead of implying a plan exists.
 */

interface Check {
  id: string;
  holds: boolean;
  claim: string;
  evidence: string;
}

export default function DeliveryGeofenceVerdict({ tripNo }: { tripNo: number | string }) {
  const { data, loading, error } = useApi(() => getTripDeliveryGeofence(tripNo), [tripNo]);

  if (loading) {
    return (
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 mb-6"><Spinner /></div>
    );
  }
  if (error || !data) return null;

  const checks: Check[] = data.checks || [];
  const matches: boolean = data.matches_complaint;
  const held = checks.filter(c => c.holds).length;

  return (
    <div className={`rounded-xl border p-5 mb-6 ${
      matches ? 'border-amber-900/60 bg-amber-950/15' : 'border-gray-800 bg-gray-900'
    }`}>
      <div className="flex items-start justify-between gap-3 flex-wrap mb-3">
        <div className="flex items-start gap-2.5">
          {matches
            ? <MapPinOff className="w-5 h-5 text-amber-400 mt-0.5 shrink-0" />
            : <ShieldCheck className="w-5 h-5 text-gray-500 mt-0.5 shrink-0" />}
          <div>
            <h2 className="text-lg font-semibold text-white">
              {matches ? 'Delivery point is not geofenced in eTrans' : 'Delivery geofence check'}
            </h2>
            <p className="text-xs text-gray-500 mt-1 max-w-4xl">{data.verdict}</p>
          </div>
        </div>
        <span className={`text-xs px-2.5 py-1 rounded-md border shrink-0 ${
          matches ? 'text-amber-400 border-amber-900/60 bg-amber-950/30'
                  : 'text-gray-400 border-gray-800 bg-gray-950'
        }`}>
          {held} of {checks.length} checks hold
        </span>
      </div>

      <blockquote className="border-l-2 border-gray-700 pl-3 mb-4">
        <p className="text-xs text-gray-400 italic">
          “Points where GPS data being captured. Customer was not geo fenced, met expected km
          running ~90% but trip closed as ‘No geo fenced delivery locations’.”
        </p>
        <p className="text-xs text-gray-600 mt-1">— the consignor's report, checked below</p>
      </blockquote>

      <ul className="space-y-2">
        {checks.map(c => (
          <li key={c.id} className="flex items-start gap-2.5">
            {c.holds
              ? <Check className="w-4 h-4 text-emerald-400 mt-0.5 shrink-0" />
              : <X className="w-4 h-4 text-gray-600 mt-0.5 shrink-0" />}
            <div className="min-w-0">
              <p className={`text-sm ${c.holds ? 'text-gray-200' : 'text-gray-500'}`}>{c.claim}</p>
              <p className="text-xs text-gray-500 mt-0.5">{c.evidence}</p>
            </div>
          </li>
        ))}
      </ul>

      <div className="mt-4 pt-3 border-t border-gray-800 grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          ['Distance run', data.distance_km == null ? '—' : `${Math.round(data.distance_km).toLocaleString()} km`],
          ['Lane median', data.lane_median_km == null ? '—' : `${Math.round(data.lane_median_km).toLocaleString()} km`],
          ['Share of lane median', data.pct_of_lane_median == null ? '—' : `${data.pct_of_lane_median}%`],
          ['Closed as', data.close_reason ?? '—'],
        ].map(([label, value]) => (
          <div key={String(label)}>
            <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
            <p className="text-sm text-gray-200 font-medium mt-0.5 break-words">{value}</p>
          </div>
        ))}
      </div>

      {data.our_fence && (
        <p className="text-xs text-gray-500 mt-3 flex items-start gap-1.5">
          <Info className="w-3 h-3 mt-0.5 shrink-0 text-gray-600" />
          This platform does have a fence for {data.destination} —{' '}
          <span className="text-gray-400">
            {data.our_fence.name}, {(data.our_fence.radius_m / 1000).toFixed(1)} km,
            source “{data.our_fence.source}”
          </span>
          {' '}— which is how the destination reports work at all. It is ours, derived here;
          eTrans still closes the trip without one.
        </p>
      )}

      <p className="text-xs text-gray-600 mt-2">{data.baseline_note}</p>
    </div>
  );
}
