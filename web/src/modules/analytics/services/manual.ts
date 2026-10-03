import { backendApi } from './api';

/** One field of the upstream API record, and where it lands. */
export interface FeedField {
  column: string;
  /** ordered — the ingest mapper takes the first key present */
  json_keys: string[];
  parser: string;
  note: string;
}

export interface ParserNote {
  name: string;
  examples: string;
  note: string;
}

export interface Derivation {
  key: string;
  label: string;
  unit: string;
  group: string;
  formula: string;
  inputs: string[];
  /** when the value is NULL rather than zero — the part people get wrong */
  null_rule: string;
  why: string;
}

export interface ExampleStep {
  key: string;
  label: string;
  unit: string;
  formula: string;
  value: string | number | null;
  /** the raw values that went in, so the arithmetic can be redone by eye */
  inputs: Record<string, string | number | null>;
}

export interface WorkedExample {
  available: boolean;
  reason?: string;
  /** true when no trip carries every input — the example still shows the maths */
  partial?: boolean;
  trip_id?: string | number | null;
  lane?: string | null;
  transporter?: string | null;
  trip_class?: string | null;
  raw?: Record<string, string | number | null>;
  steps?: ExampleStep[];
}

export interface CoverageRow {
  column: string;
  label: string;
  measured: number;
  total: number;
  pct: number;
  by_lane: Record<string, { measured: number; total: number; pct: number }>;
}

export interface Coverage {
  total: number;
  rows: CoverageRow[];
  lanes: string[];
}

export interface CalculationManual {
  feed: {
    trip_fields: FeedField[];
    metric_fields: FeedField[];
    parsers: ParserNote[];
  };
  derivations: Derivation[];
  example: WorkedExample;
  coverage: Coverage;
  /** things the product was asked for that this feed cannot support */
  not_derivable: { name: string; reason: string; needed: string }[];
}

export interface ManualFilters {
  date_from?: string;
  date_to?: string;
}

export const getManual = (f: ManualFilters = {}) =>
  backendApi.get<CalculationManual>('/manual', { params: f });
