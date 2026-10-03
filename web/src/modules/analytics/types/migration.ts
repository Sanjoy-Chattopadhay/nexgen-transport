export interface MigrationProgress {
  running: boolean;
  phase: string;
  total_rows: number;
  processed_rows: number;
  inserted: number;
  skipped: number;
  percent: number;
  started_at: string | null;
  elapsed_seconds: number;
  error: string | null;
  dimensions: Record<string, number>;
}

export interface MigrationStatus {
  [table: string]: number;
}
