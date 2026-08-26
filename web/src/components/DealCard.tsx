import { useState } from 'react';
import type { Deal } from '../types';

const money = (n: number): string => {
  const abs = Math.abs(n);
  if (abs >= 10_000) return `${n < 0 ? '-' : ''}$${Math.round(abs / 100) / 10}k`;
  return `${n < 0 ? '-' : ''}$${Math.round(abs).toLocaleString()}`;
};

const pct = (n: number): string => `${Math.round(n * 100)}%`;

function ageLabel(postedAt: number): string {
  const min = Math.max(0, Math.round((Date.now() - postedAt) / 60_000));
  if (min < 1) return 'just now';
  if (min < 60) return `${min}m ago`;
  const hours = Math.round(min / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

const SOURCE_LABEL: Record<string, string> = {
  craigslist: 'Craigslist',
  facebook: 'Facebook',
  offerup: 'OfferUp',
  sample: 'Sample',
};

const COMP_LABEL: Record<string, string> = {
  ebay_sold: 'eBay sold comps',
  ebay_active: 'eBay active listings',
  sample: 'bundled sample comps',
  local: 'local comps',
  none: 'no comps',
};

function confidenceColor(v: number): string {
  if (v >= 0.65) return 'var(--good)';
  if (v >= 0.4) return 'var(--warn)';
  return 'var(--bad)';
}

interface Props {
  deal: Deal;
  onHide: (key: string) => void;
}

export function DealCard({ deal, onHide }: Props): JSX.Element {
  const [open, setOpen] = useState(false);
  const { listing, math, valuation, repair } = deal;

  // The repair figure is only meaningful when a repair is actually planned.
  const repairShown = repair.isBroken && math.partsCost > 0;
  const overall = repairShown
    ? Math.round((valuation.confidence * 0.55 + repair.confidence * 0.45) * 100) / 100
    : valuation.confidence;

  return (
    <article className="deal" data-score={deal.score >= 60 ? 'high' : 'normal'}>
      <div className="deal-head">
        {listing.imageUrl ? (
          <img className="deal-thumb" src={listing.imageUrl} alt="" loading="lazy" />
        ) : (
          <div className="deal-thumb deal-thumb-placeholder" aria-hidden="true">
            📦
          </div>
        )}

        <div className="deal-main">
          <h3 className="deal-title">{listing.title}</h3>

          <div className="deal-profit">
            <span className="amount">{money(math.netProfit)}</span>
            <span className="roi">
              profit · {Math.round(math.roi * 100)}% ROI · score {deal.score}
            </span>
          </div>

          <div className="deal-meta">
            <span>{SOURCE_LABEL[listing.sourceId] ?? listing.sourceId}</span>
            <span>{ageLabel(listing.postedAt)}</span>
            {deal.distanceMi !== null && <span>📍 {deal.distanceMi.toFixed(1)} mi</span>}
            {listing.locationName && <span>{listing.locationName}</span>}
          </div>
        </div>
      </div>

      {deal.badges.length > 0 && (
        <div className="badges">
          {deal.badges.map((b) => (
            <span key={b.id} className="badge" data-tone={b.tone} title={b.detail}>
              <span aria-hidden="true">{b.icon}</span>
              {b.label}
            </span>
          ))}
        </div>
      )}

      <div className="numbers">
        <div>
          <span className="k">Asking</span>
          <span className="v">{listing.askPrice === null ? '—' : money(listing.askPrice)}</span>
        </div>
        <div>
          <span className="k">Resells for</span>
          <span className="v">{money(math.expectedSalePrice)}</span>
        </div>
        <div>
          <span className="k">{repairShown ? 'Parts' : 'Fees + ship'}</span>
          <span className="v">
            {repairShown
              ? money(math.partsCost)
              : money(math.marketplaceFees + math.shippingCost + math.suppliesCost)}
          </span>
        </div>
      </div>

      <div className="confidence-row">
        <div className="conf">
          <div className="conf-label">
            <span>Sell price confidence</span>
            <span>{pct(valuation.confidence)}</span>
          </div>
          <div className="conf-bar">
            <div
              className="conf-fill"
              style={{
                width: `${Math.round(valuation.confidence * 100)}%`,
                background: confidenceColor(valuation.confidence),
              }}
            />
          </div>
        </div>

        {repairShown && (
          <div className="conf">
            <div className="conf-label">
              <span>Repair cost confidence</span>
              <span>{pct(repair.confidence)}</span>
            </div>
            <div className="conf-bar">
              <div
                className="conf-fill"
                style={{
                  width: `${Math.round(repair.confidence * 100)}%`,
                  background: confidenceColor(repair.confidence),
                }}
              />
            </div>
          </div>
        )}
      </div>

      <div className="deal-actions">
        <a className="btn btn-primary" href={listing.url} target="_blank" rel="noreferrer">
          Open listing
        </a>
        <button className="btn" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? 'Hide math' : 'Show math'}
        </button>
        <button className="btn btn-ghost" onClick={() => onHide(deal.key)} title="Hide this deal">
          ✕
        </button>
      </div>

      {open && (
        <div className="breakdown">
          <table>
            <tbody>
              <tr>
                <td>Resale value ({COMP_LABEL[valuation.compSource]})</td>
                <td>{money(valuation.resaleValue)}</td>
              </tr>
              {deal.adjustments.map((a) => (
                <tr key={a.id} data-kind="cost">
                  <td>− {a.id.replace(/-/g, ' ')} ({a.matched})</td>
                  <td>−{pct(a.pct)}</td>
                </tr>
              ))}
              {math.expectedSalePrice !== valuation.resaleValue && (
                <tr>
                  <td>Expected sale price</td>
                  <td>{money(math.expectedSalePrice)}</td>
                </tr>
              )}
              <tr data-kind="cost">
                <td>Marketplace fees</td>
                <td>−{money(math.marketplaceFees)}</td>
              </tr>
              {math.shippingCost > 0 && (
                <tr data-kind="cost">
                  <td>Shipping + packaging</td>
                  <td>−{money(math.shippingCost + math.suppliesCost)}</td>
                </tr>
              )}
              {math.partsCost > 0 && (
                <tr data-kind="cost">
                  <td>
                    Repair parts (${Math.round(repair.partsLow)}–${Math.round(repair.partsHigh)})
                  </td>
                  <td>−{money(math.partsCost)}</td>
                </tr>
              )}
              <tr data-kind="cost">
                <td>Purchase price</td>
                <td>−{money(math.askPrice)}</td>
              </tr>
              {math.travelCost > 0 && (
                <tr data-kind="cost">
                  <td>Fuel (round trip)</td>
                  <td>−{money(math.travelCost)}</td>
                </tr>
              )}
              <tr data-kind="total">
                <td>Net profit (your labour excluded)</td>
                <td>{money(math.netProfit)}</td>
              </tr>
            </tbody>
          </table>

          <div className="note">
            Overall confidence <strong>{pct(overall)}</strong>. Resale is the median of{' '}
            {valuation.compCount} comparable {valuation.compSource === 'ebay_sold' ? 'sales' : 'listings'}
            {valuation.compCount > 0 && (
              <> spanning {money(valuation.resaleLow)}–{money(valuation.resaleHigh)}</>
            )}
            , matched on “{valuation.matchQuery}”.
            {valuation.soldPerWeek !== null && <> About {valuation.soldPerWeek} sell per week.</>}
            {' '}
            Break even at an ask of <strong>{money(math.breakEvenAsk)}</strong>.
          </div>

          {repair.symptoms.length > 0 && (
            <>
              <h4>Diagnosis</h4>
              <div className="note">
                Matched: {repair.symptoms.join(', ')}.
                {repair.sellerSaysPartsOnly && ' Seller listed it as for-parts.'}
                {repair.notes.map((n) => (
                  <div key={n} style={{ marginTop: 4 }}>
                    {n}
                  </div>
                ))}
              </div>
            </>
          )}

          {listing.description && (
            <>
              <h4>Seller's description</h4>
              <div className="note">{listing.description.slice(0, 400)}</div>
            </>
          )}
        </div>
      )}
    </article>
  );
}
