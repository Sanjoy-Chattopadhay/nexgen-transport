import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Activity, ArrowLeft, FileText, Hexagon, X } from 'lucide-react';
import { tripFromPath } from './nav';

/**
 * The trip workspace.
 *
 * One trip has three views that used to live in two applications: its summary
 * (status, transit, breaks), its journey analysis (the GPS trace, waypoints,
 * plant delays, weather) and its fence timeline (every fence it entered, the
 * trip as a number line, the route against its plan). They are tabs here, so
 * moving between them keeps the trip.
 *
 * Above them, the trips opened in this browser tab stay open as chips, so
 * comparing two trips is a click rather than a search. The list lives in
 * sessionStorage: it is per tab and forgotten when the tab closes.
 */

type View = 'summary' | 'analysis' | 'fences';

const VIEWS: { view: View; label: string; icon: typeof FileText; to: (no: string) => string }[] = [
  { view: 'summary', label: 'Summary', icon: FileText, to: no => `/trips/${encodeURIComponent(no)}` },
  { view: 'analysis', label: 'Journey analysis', icon: Activity, to: no => `/trips/${encodeURIComponent(no)}/analysis` },
  { view: 'fences', label: 'Fences, phases & route', icon: Hexagon, to: no => `/geo/trips/${encodeURIComponent(no)}` },
];

const KEY = 'nexgen.openTrips';
const MAX_OPEN = 8;

function load(): string[] {
  try {
    const v = JSON.parse(window.sessionStorage.getItem(KEY) || '[]');
    return Array.isArray(v) ? v.map(String).slice(-MAX_OPEN) : [];
  } catch {
    return [];
  }
}

function save(list: string[]) {
  try { window.sessionStorage.setItem(KEY, JSON.stringify(list)); } catch { /* not kept */ }
}

export default function TripTabs() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const trip = tripFromPath(pathname);
  const tripNo = trip?.tripNo ?? null;
  const [open, setOpen] = useState<string[]>(load);

  useEffect(() => {
    if (!tripNo) return;
    setOpen(prev => {
      if (prev.includes(tripNo)) return prev;
      const next = [...prev, tripNo].slice(-MAX_OPEN);
      save(next);
      return next;
    });
  }, [tripNo]);

  if (!trip) return null;
  const current = VIEWS.find(v => v.view === trip.view)!;

  const close = (no: string) => {
    const idx = open.indexOf(no);
    const next = open.filter(n => n !== no);
    setOpen(next);
    save(next);
    if (no === trip.tripNo) {
      const neighbour = next[Math.min(idx, next.length - 1)];
      navigate(neighbour ? current.to(neighbour) : '/trips');
    }
  };

  return (
    <div className="mb-5 -mt-1 border-b border-gray-800">
      <div className="flex items-center gap-1.5 overflow-x-auto pb-2.5">
        <Link to="/trips"
          className="flex items-center gap-1 px-2.5 py-1 rounded-md text-sm text-gray-400 hover:text-gray-200 hover:bg-gray-800 shrink-0">
          <ArrowLeft className="w-4 h-4" /> All trips
        </Link>
        <span className="w-px h-5 bg-gray-800 mx-1 shrink-0" />
        {open.map(no => {
          const active = no === trip.tripNo;
          return (
            <span key={no}
              className={`flex items-center rounded-md border text-sm shrink-0 ${active
                ? 'border-blue-500/60 bg-blue-600/10 text-blue-300' : 'border-gray-800 bg-gray-900 text-gray-400 hover:text-gray-200'}`}>
              <Link to={current.to(no)} className="pl-2.5 pr-1.5 py-1 tabular" aria-current={active ? 'page' : undefined}>
                Trip {no}
              </Link>
              <button onClick={() => close(no)} title={`Close trip ${no}`} aria-label={`Close trip ${no}`}
                className="pr-1.5 pl-0.5 py-1 text-gray-500 hover:text-gray-200">
                <X className="w-3.5 h-3.5" />
              </button>
            </span>
          );
        })}
      </div>
      <nav aria-label={`Trip ${trip.tripNo} views`} className="flex gap-1 overflow-x-auto">
        {VIEWS.map(v => {
          const active = v.view === trip.view;
          return (
            <Link key={v.view} to={v.to(trip.tripNo)} aria-current={active ? 'page' : undefined}
              className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${
                active ? 'border-blue-500 text-blue-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
              <v.icon className="w-4 h-4" />
              {v.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
