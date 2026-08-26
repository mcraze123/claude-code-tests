import { createRequire } from 'node:module';
import type { Section } from '../types.js';
import { containsPhrase, normalize } from '../util/text.js';
import { round2 } from '../util/geo.js';

const require = createRequire(import.meta.url);
// JSON lives outside src/ so it stays editable next to the built server.
const economics = require('../../data/economics.json') as Economics;

interface FeeTier {
  upTo: number | null;
  pct: number;
}
interface FeeModel {
  label: string;
  tiers: FeeTier[];
  perOrderFixed: number;
  perOrderFixedUnder10: number;
  flatFee: number;
}
interface SectionEconomics {
  feeModel: string;
  resaleChannel: 'ebay' | 'local';
  shipsWell: boolean;
  localHaircut: number;
}
interface ShippingBand {
  maxLb: number | null;
  cost: number;
  supplies: number;
  localOnly?: boolean;
}
interface WeightRule {
  lb: number;
  match: string[];
}
interface ResaleHaircut {
  id: string;
  match: string[];
  pct: number;
  note: string;
}
interface Economics {
  feeModels: Record<string, FeeModel>;
  sections: Record<Section, SectionEconomics>;
  shippingTable: ShippingBand[];
  weightRules: WeightRule[];
  resaleHaircuts: ResaleHaircut[];
}

/** Total haircut is capped so one badly-worded ad cannot zero a valuation. */
const MAX_TOTAL_HAIRCUT = 0.6;

export interface AppliedHaircut {
  id: string;
  pct: number;
  note: string;
  matched: string;
}

/**
 * Comp medians describe a clean, complete, clean-titled example. Listings that
 * say otherwise do not sell for that. These reductions compound.
 */
export function resaleHaircuts(text: string): {
  factor: number;
  applied: AppliedHaircut[];
} {
  const hay = normalize(text);
  const applied: AppliedHaircut[] = [];
  let factor = 1;

  for (const rule of economics.resaleHaircuts ?? []) {
    const matched = rule.match.find((m) => containsPhrase(hay, m));
    if (!matched) continue;
    applied.push({ id: rule.id, pct: rule.pct, note: rule.note, matched });
    factor *= 1 - rule.pct;
  }

  return { factor: Math.max(1 - MAX_TOTAL_HAIRCUT, round2(factor)), applied };
}

export function sectionEconomics(section: Section): SectionEconomics {
  return economics.sections[section];
}

/**
 * Marketplace fees on a sale, charged on the full amount the buyer pays.
 *
 * `adRatePct` is your promoted-listings rate, which eBay takes on top of the
 * final value fee. Leave it at 0 if you do not promote.
 */
export function marketplaceFees(
  salePrice: number,
  shippingCharged: number,
  section: Section,
  adRatePct = 0,
): number {
  const model = feeModelFor(section);
  const total = Math.max(0, salePrice + shippingCharged);
  if (total === 0) return 0;

  let fee = model.flatFee;
  let remaining = total;
  let floor = 0;
  for (const tier of model.tiers) {
    const ceiling = tier.upTo ?? Infinity;
    const slice = Math.min(remaining, Math.max(0, ceiling - floor));
    if (slice <= 0) break;
    fee += slice * tier.pct;
    remaining -= slice;
    floor = ceiling;
    if (remaining <= 0) break;
  }

  if (model.perOrderFixed > 0) {
    fee += total < 10 ? model.perOrderFixedUnder10 : model.perOrderFixed;
  }
  if (adRatePct > 0) fee += salePrice * adRatePct;

  return round2(fee);
}

function feeModelFor(section: Section): FeeModel {
  const key = economics.sections[section]?.feeModel ?? 'default';
  const model = economics.feeModels[key] ?? economics.feeModels.default;
  if (!model) throw new Error(`economics.json is missing a "default" fee model`);
  return model;
}

export interface Fulfillment {
  weightLb: number;
  shippingCost: number;
  suppliesCost: number;
  /** Too big/fragile to ship: sells to local buyers only. */
  localOnly: boolean;
}

/** Guess the shipped weight from the title, then price the box. */
export function estimateFulfillment(title: string, section: Section): Fulfillment {
  const hay = normalize(title);
  let weightLb = 5; // unknown small-electronics default
  for (const rule of economics.weightRules) {
    if (rule.match.some((m) => hay.includes(normalize(m)))) {
      weightLb = rule.lb;
      break;
    }
  }
  if (!economics.sections[section].shipsWell) weightLb = 999;

  const band =
    economics.shippingTable.find((b) => b.maxLb !== null && weightLb <= b.maxLb) ??
    economics.shippingTable[economics.shippingTable.length - 1]!;

  return {
    weightLb,
    shippingCost: band.localOnly ? 0 : band.cost,
    suppliesCost: band.localOnly ? 0 : band.supplies,
    localOnly: Boolean(band.localOnly),
  };
}

/**
 * How much of the eBay comp price a local cash sale actually realises.
 * Local buyers pay less than a nationwide eBay audience, and appliances and
 * cars are haggled hard.
 */
export function localHaircut(section: Section, localOnly: boolean): number {
  const cfg = economics.sections[section];
  if (cfg.resaleChannel === 'local') return cfg.localHaircut;
  // An electronics item that cannot ship (a 65" TV) still sells locally only.
  return localOnly ? 0.2 : 0;
}

/** Exposed for the UI's "how was this calculated" panel. */
export function feeModelLabel(section: Section): string {
  return feeModelFor(section).label;
}
