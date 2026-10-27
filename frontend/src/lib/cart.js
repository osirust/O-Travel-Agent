import { offerAmount } from './travel.js';

// Add only comparable, whole-trip amounts; nightly and unknown prices stay separate.
export function cartTotal(items) {
  const flight = items?.flights, hotel = items?.hotels;
  const entries = [[flight, ['INDICATIVE_ITINERARY_TOTAL', 'GROUP_ITINERARY_TOTAL']], [hotel, ['INDICATIVE_STAY_TOTAL', 'PROVIDER_ROOMS_STAY_TOTAL']]].filter(([offer]) => offer);
  if (!entries.length) return null;
  const currency = entries[0][0].currency;
  if (!currency || entries.some(([offer, kinds]) => offer.currency !== currency || !kinds.includes(offer.price_kind))) return null;
  let amount = 0;
  for (const [offer] of entries) {
    const price = offerAmount(offer);
    if (!Number.isSafeInteger(price) || price < 0 || !Number.isSafeInteger(amount + price)) return null;
    amount += price;
  }
  return { amount, currency };
}
