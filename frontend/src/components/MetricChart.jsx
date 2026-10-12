import { useState } from "react";

const VB_W = 600;
const VB_H = 180;
const PAD = { top: 16, right: 16, bottom: 28, left: 44 };

function scales(series) {
  const values = series.map((p) => p.value);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (min === max) {
    // A flat series (e.g. one point, or every attempt tied) still needs a
    // non-zero domain so the line/markers don't collapse onto one edge.
    min -= 1;
    max += 1;
  }
  const pad = (max - min) * 0.12;
  min -= pad;
  max += pad;

  const innerW = VB_W - PAD.left - PAD.right;
  const innerH = VB_H - PAD.top - PAD.bottom;

  const x = (i) => PAD.left + (series.length === 1 ? innerW / 2 : (i / (series.length - 1)) * innerW);
  const y = (v) => PAD.top + innerH - ((v - min) / (max - min)) * innerH;
  return { x, y, min, max };
}

export default function MetricChart({ series, metricName }) {
  const [hoverIndex, setHoverIndex] = useState(null);

  if (series.length === 0) return null;

  const { x, y, min, max } = scales(series);
  const linePath = series.map((p, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(p.value)}`).join(" ");
  const last = series[series.length - 1];
  const hovered = hoverIndex !== null ? series[hoverIndex] : null;

  function onMove(e) {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * VB_W;
    let nearest = 0;
    let best = Infinity;
    series.forEach((_, i) => {
      const d = Math.abs(x(i) - px);
      if (d < best) {
        best = d;
        nearest = i;
      }
    });
    setHoverIndex(nearest);
  }

  return (
    <div className="chart">
      <svg
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        className="chart-svg"
        onMouseMove={onMove}
        onMouseLeave={() => setHoverIndex(null)}
      >
        {[min, (min + max) / 2, max].map((v, i) => (
          <g key={i}>
            <line x1={PAD.left} x2={VB_W - PAD.right} y1={y(v)} y2={y(v)} className="chart-gridline" />
            <text x={PAD.left - 8} y={y(v)} className="chart-axis-label" textAnchor="end" dominantBaseline="middle">
              {v.toFixed(2)}
            </text>
          </g>
        ))}

        {hovered && (
          <line x1={x(hoverIndex)} x2={x(hoverIndex)} y1={PAD.top} y2={VB_H - PAD.bottom} className="chart-crosshair" />
        )}

        <path d={linePath} className="chart-line" />

        {series.map((p, i) => (
          <g key={i}>
            <circle cx={x(i)} cy={y(p.value)} r={7} className="chart-marker-ring" />
            <circle cx={x(i)} cy={y(p.value)} r={5} className={p.kept ? "chart-marker kept" : "chart-marker"} />
          </g>
        ))}

        <text x={x(series.length - 1) + 8} y={y(last.value)} className="chart-end-label" dominantBaseline="middle">
          {last.value}
        </text>
      </svg>

      {hovered && (
        <div
          className="chart-tooltip"
          style={{ left: `${(x(hoverIndex) / VB_W) * 100}%`, top: `${(y(hovered.value) / VB_H) * 100}%` }}
        >
          <strong>
            {metricName} {hovered.value}
          </strong>
          <span>
            attempt {hovered.iteration + 1} — {hovered.kept ? "kept" : "reverted"}
          </span>
        </div>
      )}
    </div>
  );
}
