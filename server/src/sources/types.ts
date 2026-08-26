import type { RawListing, Section } from '../types.js';

export interface SearchRequest {
  /** One keyword phrase from a profile. Adapters run one search per keyword. */
  query: string;
  section: Section;
  minPrice: number | null;
  maxPrice: number | null;
  lat: number | null;
  lon: number | null;
  radiusMi: number;
  limit: number;
}

export interface SourceAdapter {
  id: string;
  label: string;
  /** Where the operator goes to fix an unavailable source. */
  docsAnchor: string;
  /** False when required configuration is missing. */
  isAvailable(): boolean;
  /** Why it is unavailable, shown verbatim in the UI. */
  unavailableReason(): string | null;
  search(req: SearchRequest): Promise<RawListing[]>;
}
