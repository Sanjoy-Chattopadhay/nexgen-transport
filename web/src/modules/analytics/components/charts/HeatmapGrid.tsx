import { Fragment } from 'react';

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

interface DataPoint { hour_of_day: number; day_of_week: number; [key: string]: any; }
interface Props { data: DataPoint[]; valueKey: string; label: string; }

export default function HeatmapGrid({ data, valueKey, label }: Props) {
  const maxVal = Math.max(...data.map(d => d[valueKey] || 0), 1);

  const getCell = (dow: number, hour: number) => {
    const point = data.find(d => d.day_of_week === dow && d.hour_of_day === hour);
    return point ? point[valueKey] : 0;
  };

  const getColor = (val: number) => {
    if (val === 0) return 'bg-gray-800/50';
    const intensity = val / maxVal;
    if (intensity < 0.25) return 'bg-blue-900/40';
    if (intensity < 0.5) return 'bg-blue-800/50';
    if (intensity < 0.75) return 'bg-blue-600/50';
    return 'bg-blue-500/60';
  };

  return (
    <div>
      <p className="text-sm text-gray-400 mb-3">{label}</p>
      <div className="overflow-x-auto">
        <div className="inline-grid gap-0.5" style={{ gridTemplateColumns: `60px repeat(24, 1fr)` }}>
          <div />
          {Array.from({ length: 24 }, (_, h) => (
            <div key={h} className="text-center text-xs text-gray-500 pb-1">{h}</div>
          ))}
          {DAYS.map((day, dow) => (
            <Fragment key={day}>
              <div className="text-xs text-gray-400 flex items-center pr-2">{day}</div>
              {Array.from({ length: 24 }, (_, h) => {
                const val = getCell(dow + 1, h);
                return (
                  <div key={`${dow}-${h}`} className={`w-6 h-6 rounded-sm ${getColor(val)} flex items-center justify-center`}
                    title={`${day} ${h}:00 — ${val.toFixed(1)}`}>
                    {val > 0 && <span className="text-xs text-gray-300">{Math.round(val)}</span>}
                  </div>
                );
              })}
            </Fragment>
          ))}
        </div>
      </div>
    </div>
  );
}
