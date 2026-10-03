/**
 * The link between a KPI tile and the grid it sits in: the grid owns which
 * tile's dropdown is open and draws the dropdown full-width beneath the
 * tiles, where a table has room. Kept apart from ui.tsx and drill.tsx so
 * neither has to import the other.
 */
import { createContext, type ReactNode } from 'react';
import type { Params } from '../lib/api';

export interface DrillSpec {
  /** A dataset of GET /api/v1/drill/{dataset}. */
  dataset: string;
  /** The page's context and the dataset's preset, measure, metric… */
  params?: Params;
  /** Breakdowns offered, the first shown; default: every one the dataset has. */
  groups?: string[];
  /** Default sort of the records, and its direction. */
  sort?: string;
  order?: 'asc' | 'desc';
  /** Which figure of the answer reproduces the tile. */
  headline?: 'value' | 'groups' | 'p50' | 'rows';
  /** How the number is counted, in a sentence. */
  note?: ReactNode;
}

export interface OpenKPI {
  id: string;
  label: string;
  value: ReactNode;
  drill?: DrillSpec;
  details?: ReactNode | (() => ReactNode);
}

export const KPIGridContext = createContext<{
  open: OpenKPI | null;
  toggle: (k: OpenKPI) => void;
} | null>(null);
