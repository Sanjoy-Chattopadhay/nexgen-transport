interface Props {
  percent: number;
  label?: string;
  color?: string;
}

export default function ProgressBar({ percent, label, color = 'bg-blue-500' }: Props) {
  return (
    <div>
      {label && <div className="flex justify-between text-sm mb-1"><span className="text-gray-400">{label}</span><span className="text-gray-300 font-medium">{percent.toFixed(1)}%</span></div>}
      <div className="w-full h-3 bg-gray-800 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all duration-500 ease-out`} style={{ width: `${Math.min(100, percent)}%` }} />
      </div>
    </div>
  );
}
