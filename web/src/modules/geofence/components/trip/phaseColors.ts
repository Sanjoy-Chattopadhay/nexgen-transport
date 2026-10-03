/**
 * One colour language for a trip's phases, used by the number line, the
 * small bars in lists and the 3D space-time view alike -- so loading is the
 * same violet everywhere and nobody has to read a legend twice.
 */
import { PALETTE } from '../../lib/theme';

export type Phase = 'before' | 'loading' | 'transit' | 'unloading' | 'after' | 'place';
export type Kind = 'stay' | 'moving' | 'stop' | 'halt' | 'silent';

export const PHASE_LABEL: Record<string, string> = {
  before: 'Before loading', loading: 'Loading', transit: 'Transit', unloading: 'Unloading',
  after: 'After unloading', place: 'At one place',
};

export const KIND_TEXT: Record<string, string> = {
  stay: 'at the place', moving: 'driving', stop: 'stopped outside every fence', halt: 'halt at an intermediate place',
  silent: 'GPS silent while moving',
};

/** Colour of a segment: stays by their phase, the rest by what filled them. */
export function segmentColor(phase: string, kind: string): string {
  if (kind === 'stay') {
    if (phase === 'loading') return PALETTE.purple;
    if (phase === 'unloading') return PALETTE.cyan;
    return PALETTE.pairAlt;
  }
  if (kind === 'halt') return PALETTE.green;
  if (kind === 'stop') return PALETTE.amber;
  if (kind === 'silent') return PALETTE.red;
  return PALETTE.blue;
}

/** Before loading and after unloading are drawn fainter: not the trip proper. */
export function segmentOpacity(phase: string): number {
  return phase === 'before' || phase === 'after' ? 0.4 : 0.95;
}

export const LEGEND: { label: string; phase: string; kind: string }[] = [
  { label: 'Loading', phase: 'loading', kind: 'stay' },
  { label: 'Driving', phase: 'transit', kind: 'moving' },
  { label: 'Stopped', phase: 'transit', kind: 'stop' },
  { label: 'Halt at a place', phase: 'transit', kind: 'halt' },
  { label: 'GPS silent', phase: 'transit', kind: 'silent' },
  { label: 'Unloading', phase: 'unloading', kind: 'stay' },
];
