/**
 * Method notes for figures.
 *
 * Every chart on this platform should be able to answer three questions without
 * anyone opening the code:
 *
 *   1. What is the number?      -- the formula, which is specific to the figure
 *   2. How was it drawn?        -- the plotting rules, which are shared by every
 *                                  chart of the same TYPE
 *   3. How do I read it?        -- what a shape means, and the trap to avoid
 *
 * (2) and (3) repeat across a hundred charts, so they live here once, keyed by
 * chart type, and a figure supplies only its own formula. That keeps the notes
 * consistent — a box plot is read the same way wherever it appears — and stops
 * them rotting into a hundred slightly different descriptions of the same axis.
 */

export type PlotKind =
  | 'histogram' | 'box' | 'heatmap' | 'bubble' | 'quadrant' | 'dualAxis'
  | 'line' | 'bar' | 'hbar' | 'donut' | 'gauge' | 'funnel' | 'ecdf'
  | 'map' | 'table' | 'matrix' | 'radar' | 'stackedBar';

export interface PlotNote {
  plotted: string;
  read: string;
}

export const PLOT_NOTES: Record<PlotKind, PlotNote> = {
  histogram: {
    plotted: 'The x-axis is the measured value, split into equal-width bins; the height of each bar is how much fell in that bin. Bin width is chosen from the row count (√n, capped), so a wider window gives finer bars, not taller ones.',
    read: 'Look at the SHAPE, not the peak. One tall narrow cluster means the process is predictable. A long right tail means the average is optimistic — the tail is what actually breaks promises, and it is the reason a mean and a median differ here.',
  },
  box: {
    plotted: 'The box spans the 25th to 75th percentile (the middle half of the data) with the median as the line inside it. Whiskers reach the furthest point still within 1.5 × the box height; anything past that is drawn as an individual outlier dot.',
    read: 'A short box is a predictable partner or lane. A tall box means the same job takes wildly different times, so the average cannot be planned against. Many dots beyond the whisker means rare, severe blow-ups rather than general slowness.',
  },
  heatmap: {
    plotted: 'Rows and columns are the two dimensions; each cell is coloured by its value, scaled across the whole grid so identical shades mean identical values. An empty cell means no trips matched that combination — it is not a zero.',
    read: 'Read along a row to see one thing change over time, and down a column to compare things at one moment. Bands and blocks are the finding; a single dark cell is usually a small sample.',
  },
  bubble: {
    plotted: 'Each bubble is one entity placed at (x, y). Bubble AREA — not radius — carries the size measure, which is the perceptually honest encoding: a bubble with four times the area represents four times the quantity.',
    read: 'Position tells you the trade-off; size tells you how much it matters. A bad position on a tiny bubble is a rounding error, the same position on a large one is the finding.',
  },
  quadrant: {
    plotted: 'Two reference lines cut the plane into four. Colour is the quadrant a point falls in, deliberately not a continuous scale — the output is which of four actions applies, and a gradient would invite splitting hairs between two points on the same side of both lines.',
    read: 'Work the quadrants, not the individual positions. The line placement is stated on the chart; move it and points move with it, so check what the split is before drawing a conclusion.',
  },
  dualAxis: {
    plotted: 'Two series on one x-axis with independent y-scales — the left axis belongs to the bars/area, the right to the line. The two scales are unrelated, so the point at which the lines visually cross means nothing.',
    read: 'Read the two shapes against each other, not their crossing points. Bars rising while the line falls is the pattern worth acting on: more volume being served worse.',
  },
  line: {
    plotted: 'One point per period, joined in time order. Gaps are periods with no data and are not interpolated — a flat segment across a gap would invent trips that did not run.',
    read: 'Direction over several periods is the signal; a single period moving is usually sample size. Compare the slope against the volume series before calling it a service change.',
  },
  bar: {
    plotted: 'One bar per category, height is the value, all bars on one shared scale starting at zero so lengths are directly comparable.',
    read: 'Compare lengths, and check the category count — a bar chart of the top N hides the tail, so a small bar here can still be a large absolute number.',
  },
  hbar: {
    plotted: 'Horizontal bars ranked by value, longest first. Where a colour scale is applied, it encodes a SECOND metric — so length and colour are two independent facts about the same row.',
    read: 'Length is "how much", colour is "how well". A long red bar is the priority: a lot of something, being done badly. A short red bar rarely is.',
  },
  donut: {
    plotted: 'Each arc is one category, its angle proportional to its share of the total. Slices are ordered by size, and small categories may be grouped.',
    read: 'Use it for "is this concentrated or spread", nothing finer — human eyes compare angles badly. For ranking two similar slices, read the table instead.',
  },
  gauge: {
    plotted: 'A single value against a fixed target marker on a 0–100 scale. The target is a business promise, not a data-derived threshold.',
    read: 'Only the gap to the target matters. The colour changes at the target, so a needle just below it is not meaningfully worse than one just above — check the trend before reacting.',
  },
  funnel: {
    plotted: 'One bar per lifecycle milestone, each showing how many trips recorded that milestone. Stages are ordered by when they happen, not by size.',
    read: 'A step-down means trips that never RECORDED the next milestone — a tracking gap, not vanished trucks. Large drops are data-quality findings and undermine every metric downstream of that stage.',
  },
  ecdf: {
    plotted: 'For each value on the x-axis, the y-axis is the share of observations at or below it. It is the running total of the histogram, so it never falls.',
    read: 'Read it as "what fraction finish within X". The steep part is where most of the mass sits; a long flat tail on the right is the small share of very bad cases.',
  },
  map: {
    plotted: 'Each circle sits on the geographic centre of its area, sized by volume and coloured by the chosen metric. A centroid is not where the freight physically went — it represents the whole area.',
    read: 'Size first, colour second. Areas with too few judged trips are drawn grey rather than coloured, because a rate on a handful of trips is not a finding.',
  },
  table: {
    plotted: 'One row per entity, sorted by the column indicated in the header. Percentages are rounded for display; totals are computed on the unrounded values, so a column may not appear to add up exactly.',
    read: 'Check the row count behind a rate before acting on it. Where a confidence band is shown, two rows whose bands overlap have not been shown to differ.',
  },
  matrix: {
    plotted: 'A square grid of pairwise Pearson correlations, from −1 through 0 to +1, coloured divergently around zero. The diagonal is always 1.00 because a metric correlates perfectly with itself.',
    read: 'Read the off-diagonal cells only. Strong correlation narrows where to look; it is not causation, and two metrics can move together because a third drives both.',
  },
  radar: {
    plotted: 'Each spoke is one metric, normalised 0–100 across the entities shown so they can share an axis. Metrics where lower is better are inverted, so on this chart further out is always better.',
    read: 'Compare shapes, not areas. A lopsided shape is the weakness worth raising; the normalisation is relative to the entities on screen, so adding or removing one moves everybody.',
  },
  stackedBar: {
    plotted: 'Segments stack to the category total, each segment being one component. Segment order is constant across bars so the same component is always in the same place.',
    read: 'The total is easy to compare; the middle segments are not, because they do not share a baseline. Compare the bottom segment across bars, and use the total for everything else.',
  },
};

export interface FigureMethod {
  /** How the number is computed — specific to this figure. */
  formula: string;
  /** Which shared plotting/reading note applies. */
  plot: PlotKind;
  /** Optional extra caveat unique to this figure. */
  caveat?: string;
}
