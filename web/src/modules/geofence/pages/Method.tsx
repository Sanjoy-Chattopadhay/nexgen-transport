import type { ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ArrowDown, BookOpen, Crosshair, Database, Filter, Hexagon, Library, Network, Ruler, Sigma, SlidersHorizontal } from 'lucide-react';
import { Card, Note, PageHeader, Segmented } from '../components/ui';
import { GuideView, guideAvailable } from '../components/guide/Guide';

type View = 'guide' | 'method';

function Step({ icon: Icon, title, children, tone = 'blue' }: {
  icon: typeof Filter; title: string; children: ReactNode; tone?: 'blue' | 'purple' | 'amber' | 'green';
}) {
  const color = { blue: 'text-blue-400 bg-blue-950/40', purple: 'text-purple-300 bg-purple-950/40',
    amber: 'text-amber-400 bg-amber-950/40', green: 'text-emerald-400 bg-emerald-950/40' }[tone];
  return (
    <div className="flex gap-4">
      <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${color}`}><Icon className="w-5 h-5" /></div>
      <div className="min-w-0">
        <h3 className="text-sm font-semibold text-white">{title}</h3>
        <div className="text-sm text-gray-400 mt-1 space-y-2 leading-relaxed">{children}</div>
      </div>
    </div>
  );
}

function Arrow() {
  return <div className="pl-3 text-gray-700"><ArrowDown className="w-4 h-4" /></div>;
}

/**
 * Two views of the same question. The guide walks every page, figure and
 * number with screenshots; the method in brief is the pipeline on one screen.
 */
export default function Method() {
  const [params, setParams] = useSearchParams();
  const asked = params.get('view');
  const view: View = asked === 'method' || asked === 'guide' ? asked : guideAvailable ? 'guide' : 'method';
  const choose = (v: View) => setParams(v === (guideAvailable ? 'guide' : 'method') ? {} : { view: v }, { replace: true });

  return (
    <div className={`animate-fade-in ${view === 'method' ? 'max-w-5xl' : ''}`}>
      <PageHeader title="How it works"
        subtitle={view === 'guide'
          ? 'Every page of this application explained: every number, chart, map and table, the database columns behind it, and exactly how each value is worked out.'
          : "From a raw GPS fix to a geofence visit on these pages, and why each step is there. Every threshold was measured on this fleet's own 5.1 million fixes, not assumed."}
        actions={
          <Segmented<View> value={view} onChange={choose} options={[
            { value: 'guide', label: 'Illustrated guide', icon: Library },
            { value: 'method', label: 'Method in brief', icon: BookOpen },
          ]} />
        } />
      {view === 'guide' ? <GuideView /> : <MethodInBrief />}
    </div>
  );
}

function MethodInBrief() {
  return (
    <>
      <Card title="The pipeline" icon={BookOpen} className="mb-6">
        <div className="space-y-3">
          <Step icon={Database} title="1. Raw GPS, kept unchanged">
            <p>Every fix the trackers send is stored as received. Nothing downstream overwrites it, so any visit can be
              traced back to the fixes it rests on, and reprocessed when settings or maps change.</p>
          </Step>
          <Arrow />
          <Step icon={Filter} title="2. Clean: refuse the impossible">
            <p>Fixes with no position, repeats of the same second, coordinates outside India, and teleports — fixes implying
              a speed no truck can reach that nothing after them agrees with — are refused. Fixes that merely arrive late
              are put back in time order. 0.14% of fixes.</p>
          </Step>
          <Arrow />
          <Step icon={SlidersHorizontal} title="3. Fit: the best position for each fix" tone="purple">
            <p><b className="text-gray-300">Standing still</b> is 75% of all fixes, and a parked receiver drifts: 10% of its
              fixes land more than 25 m from where the truck is, 0.57% more than 250 m. Over each standstill, every fix is
              replaced by the median of its neighbours — which removes spikes but keeps a genuine short move, such as
              weighbridge to parking bay.</p>
            <p><b className="text-gray-300">Out-and-back spikes</b> — a parked truck "jumping" 100 m to 2 km for one or two
              fixes and landing back where it was — are corrected even when the device reports a speed. About 5,500 of these
              survive any speed filter, and next to a fence line each one used to create a false exit and re-entry.</p>
            <p><b className="text-gray-300">Moving fixes</b> are left as recorded, or snapped to the road when OSRM is
              connected, but never moved more than 30 m. Timestamps are never touched.</p>
          </Step>
          <Arrow />
          <Step icon={Hexagon} title="4. Detect: crossings that held" tone="green">
            <p>A truck counts as inside a fence only once it is clearly past the boundary, and the change must hold — for
              90 seconds, or by getting 250 m past the line. That is what stops a truck parked on a fence line producing
              hundreds of in/out events.</p>
            <p><b className="text-gray-300">The band scales to the fence.</b> "Clearly past" used to be a fixed 25 m. A fence
              whose centre is under 25 m from its edge has no "clearly inside" at all, so 905 active weighbridges, gates and
              bays could never register a visit. The band is now half the fence's inscribed radius, and unchanged on large works.</p>
          </Step>
          <Arrow />
          <Step icon={Network} title="5. Infer: fences passed while the tracker was silent" tone="amber">
            <p>A hole in the trail is only missing evidence if the truck moved across it — 99.5% of this feed's 1–4 hour
              holes are a tracker asleep while the truck stood still. When the truck did move, OSRM's road path across the
              hole names the fences it must have passed. Those are shown as <i>inferred</i>, with a confidence, and are never
              counted as visits.</p>
          </Step>
          <Arrow />
          <Step icon={Sigma} title="6. Summarise">
            <p>Visits roll up per fence, per day, per trip, vehicle, transporter, lane and driver. Stay times are medians over
              fully observed visits only; nested fences are never double counted; stays are split across midnight.</p>
            <p className="mt-2">The fleet system opens one trip per consignment, so a truck carrying three invoices is three
              trips over the same GPS — 38% of trips share their truck's fixes. A trip's page shows its own view, but every
              total across trips counts a shared stay, alert or kilometre once.</p>
          </Step>
        </div>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
        <Card title="Why not snap everything to the road?" icon={Network}>
          <p className="text-sm text-gray-400 leading-relaxed">
            Snapping every fix to the road network before checking fences would make the fences Tata Steel cares most about
            less accurate. Works interiors, yards, weighbridges and parking bays are mostly off the road network, and the
            trucks standing in them are most of the data. Map matching would drag a parked truck onto the nearest road —
            out of a 40 m weighbridge fence, or into the next one. So OSRM is used where it helps: moving fixes (bounded),
            spike detection, and the road path across GPS holes.
          </p>
        </Card>
        <Card title="Reading the uncertainty marks" icon={Crosshair}>
          <ul className="text-sm text-gray-400 space-y-2 leading-relaxed">
            <li><b className="text-gray-300">± on a time</b> — the GPS gap around a crossing. The true crossing is somewhere inside it.</li>
            <li><b className="text-gray-300">≤ before a time</b> — the truck was already inside when its GPS began; the real arrival was earlier.</li>
            <li><b className="text-gray-300">+ after a stay</b> — still inside when the GPS ended; the stay is at least this long.</li>
            <li><b className="text-gray-300">escape / dwell</b> — how a crossing was confirmed: by distance past the line, or by holding long enough.</li>
          </ul>
        </Card>
      </div>

      <Card title="Setting up OSRM" icon={Ruler}>
        <p className="text-sm text-gray-400 mb-3">
          Optional, and off unless <code className="text-gray-300">OSRM_URL</code> is set. The recommended deployment is the official
          container with India's OpenStreetMap extract, preprocessed once:
        </p>
        <pre className="bg-gray-950 border border-gray-800 rounded-lg p-3 text-xs text-gray-300 overflow-x-auto">{`wget https://download.geofabrik.de/asia/india-latest.osm.pbf
docker run -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-extract -p /opt/car.lua /data/india-latest.osm.pbf
docker run -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-partition /data/india-latest.osrm
docker run -t -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend osrm-customize /data/india-latest.osrm
docker run -d -p 5000:5000 -v "$PWD:/data" ghcr.io/project-osrm/osrm-backend \\
    osrm-routed --algorithm mld --max-matching-size 500 /data/india-latest.osrm`}</pre>
        <div className="mt-3">
          <Note>Then set <code>OSRM_URL=http://&lt;server&gt;:5000</code> in <code>.env</code>, check it on the Data Quality page, and
            run a new fitted run. Compare it with the previous run there before publishing it. Full notes are in <code>docs/OSRM.md</code>.</Note>
        </div>
      </Card>
    </>
  );
}
