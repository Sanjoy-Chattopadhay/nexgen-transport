import { ResponsiveContainer, Treemap, Tooltip } from 'recharts';
import { CHART_COLORS } from '../../lib/colors';
import { metricColor } from '../../lib/colorScales';
import { tc } from '../../../../core/theme';

interface Item { name: string; size: number; color_value: number | null; [key: string]: any; }

interface Props {
  data: Item[];
  colorLabel?: string;
  colorLo?: number;
  colorHi?: number;
  height?: number;
}

function Tile(props: any) {
  const { x, y, width, height, name, color_value, colorLo, colorHi } = props;
  if (width < 4 || height < 4) return null;
  const fill = metricColor(color_value, colorLo, colorHi);
  return (
    <g>
      <rect x={x} y={y} width={width} height={height} fill={fill} fillOpacity={0.55}
        stroke={tc(tc('#111827'))} strokeWidth={2} rx={3} />
      {width > 56 && height > 24 && (
        <text x={x + 6} y={y + 16} fill={tc(tc('#f9fafb'))} fontSize={12} fontWeight={600}>
          {String(name).length > Math.floor(width / 7) ? String(name).slice(0, Math.floor(width / 7)) + '…' : name}
        </text>
      )}
    </g>
  );
}

/** Treemap: tile area = volume, tile colour = quality metric (red→green). */
export default function TreemapChart({ data, colorLabel = 'OTD %', colorLo = 50, colorHi = 100, height = 420 }: Props) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <Treemap data={data} dataKey="size" nameKey="name" isAnimationActive={false}
        content={<Tile colorLo={colorLo} colorHi={colorHi} />}>
        <Tooltip contentStyle={{ backgroundColor: CHART_COLORS.tooltipBg, border: `1px solid ${tc('#374151')}`, borderRadius: 8, color: tc('#f3f4f6') }}
          formatter={(v: any, _n: any, entry: any) => {
            const cv = entry?.payload?.color_value;
            return [`${v} trips · ${colorLabel} ${cv ?? '—'}`, entry?.payload?.name];
          }} />
      </Treemap>
    </ResponsiveContainer>
  );
}
