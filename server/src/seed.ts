import { createProfile, listProfiles, type ProfileInput } from './db/repo.js';

/**
 * Starter profiles. They are deliberately broad - the operator narrows them
 * from the UI once they see what their local market actually throws up.
 */
const DEFAULTS: ProfileInput[] = [
  {
    section: 'electronics',
    name: 'Broken & for-parts electronics',
    enabled: true,
    keywords: [
      'broken laptop', 'laptop for parts', 'cracked screen laptop', 'macbook broken',
      'graphics card not working', 'gpu no display', 'gaming pc not working',
      'tv no power', 'tv for parts', 'broken tv',
      'ps5 broken', 'xbox not working', 'nintendo switch broken',
      'iphone cracked', 'ipad cracked screen',
      'receiver not working', 'amplifier for parts', 'tube amp',
      'tube radio', 'stereo not working',
      'monitor no power', 'motherboard for parts', 'electronics lot as is',
    ],
    mustInclude: [],
    exclude: ['replica', 'case only', 'box only', 'stolen', 'wanted', 'looking for'],
    minPrice: null,
    maxPrice: 800,
    radiusMi: 35,
    minProfit: 60,
    minRoi: 0.6,
    includeBroken: true,
    minConfidence: 0.35,
    notify: true,
    sources: null,
  },
  {
    section: 'electronics',
    name: 'Working electronics under market',
    enabled: true,
    keywords: [
      'macbook pro', 'gaming pc', 'graphics card', 'rtx', 'oled tv',
      'ipad pro', 'iphone', 'nintendo switch', 'ps5', 'xbox series x',
      'stereo receiver', 'integrated amplifier', 'gaming monitor',
    ],
    mustInclude: [],
    exclude: ['wanted', 'looking for', 'iso ', 'repair service', 'rental'],
    minPrice: 20,
    maxPrice: 1500,
    radiusMi: 30,
    minProfit: 80,
    minRoi: 0.35,
    includeBroken: false,
    minConfidence: 0.45,
    notify: true,
    sources: null,
  },
  {
    section: 'appliances',
    name: 'Repairable appliances',
    enabled: true,
    keywords: [
      'washer not working', 'dryer no heat', 'refrigerator not cooling',
      'dishwasher not draining', 'washer dryer set', 'stove not working',
      'appliance repair needed', 'free appliance',
    ],
    mustInclude: [],
    exclude: ['wanted', 'looking for', 'parts only lot', 'rental'],
    minPrice: null,
    maxPrice: 400,
    radiusMi: 30,
    minProfit: 100,
    minRoi: 0.8,
    includeBroken: true,
    minConfidence: 0.3,
    notify: true,
    sources: null,
  },
  {
    section: 'vehicles',
    name: 'Mechanic specials',
    enabled: true,
    keywords: [
      'mechanic special', 'needs engine', 'wont start', 'transmission slipping',
      'project car', 'salvage title', 'needs work runs',
    ],
    mustInclude: [],
    exclude: ['wanted', 'looking for', 'parting out', 'rental', 'lease'],
    minPrice: 300,
    maxPrice: 6000,
    radiusMi: 60,
    minProfit: 800,
    minRoi: 0.4,
    includeBroken: true,
    minConfidence: 0.25,
    notify: true,
    sources: null,
  },
];

/** Creates the starter profiles on an empty database. Idempotent. */
export function seedProfiles(): number {
  if (listProfiles().length > 0) return 0;
  for (const p of DEFAULTS) createProfile(p);
  return DEFAULTS.length;
}
