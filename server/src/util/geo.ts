const EARTH_RADIUS_MI = 3958.8;

const toRad = (deg: number): number => (deg * Math.PI) / 180;

export interface Point {
  lat: number;
  lon: number;
}

/** Great-circle distance in statute miles. */
export function distanceMi(a: Point, b: Point): number {
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const lat1 = toRad(a.lat);
  const lat2 = toRad(b.lat);
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_MI * Math.asin(Math.min(1, Math.sqrt(h)));
}

/**
 * Round-trip driving cost for a pickup. Marketplace distances are as-the-crow
 * flies, and real roads are roughly 1.25x that, so the fudge is baked in.
 */
export function travelCost(distance: number | null, costPerMi: number): number {
  if (distance === null || !Number.isFinite(distance)) return 0;
  return round2(distance * 2 * 1.25 * costPerMi);
}

export function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

export function isPoint(lat: unknown, lon: unknown): boolean {
  return (
    typeof lat === 'number' &&
    typeof lon === 'number' &&
    Number.isFinite(lat) &&
    Number.isFinite(lon) &&
    Math.abs(lat) <= 90 &&
    Math.abs(lon) <= 180 &&
    !(lat === 0 && lon === 0)
  );
}
