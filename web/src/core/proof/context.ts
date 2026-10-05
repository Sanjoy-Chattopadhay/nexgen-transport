/**
 * The link between a tile and the proof grid it sits in: the grid owns which
 * figure's proof is open and draws it full width beneath the row of tiles.
 */
import { createContext, useContext } from 'react';

export interface ProofSpec {
  /** A dataset of GET /api/v1/proof/{dataset} (or of `endpoint`). */
  dataset: string;
  /** Where the datasets live, under /api/v1: '/proof' (fleet figures, the default) or another
   * service's proof in the same shape, e.g. '/geo/plants/proof'. */
  endpoint?: string;
  /** The page's own filters (date window ...); consignor and zonal/local are added for every call. */
  params?: Record<string, string | number | undefined | null>;
  /** The number on the tile, as a number, so the panel can show the recount agrees. */
  value?: number | string | null;
}

export interface OpenProof {
  id: string;
  label: string;
  display: string;
  spec: ProofSpec;
}

export const ProofContext = createContext<{
  open: OpenProof | null;
  toggle: (p: OpenProof) => void;
} | null>(null);

export const useProofGrid = () => useContext(ProofContext);
