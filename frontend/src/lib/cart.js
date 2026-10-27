import { offerAmount } from './travel.js';

// Add only comparable, whole-trip amounts; nightly and unknown prices stay separate.
export function cartTotal(items) {
  const flight = items?.flights, hotel = items?.hotels;
  if (!flight || !hotel || !flight.currency || flight.currency !== hotel.currency) return null;
  if (!['INDICATIVE_ITINERARY_TOTAL', 'GROUP_ITINERARY_TOTAL'].includes(flight.price_kind)) return null;
  if (!['INDICATIVE_STAY_TOTAL', 'PROVIDER_ROOMS_STAY_TOTAL'].includes(hotel.price_kind)) return null;
  const flightPrice = offerAmount(flight), hotelPrice = offerAmount(hotel);
  if (!Number.isSafeInteger(flightPrice) || flightPrice < 0 || !Number.isSafeInteger(hotelPrice) || hotelPrice < 0 || !Number.isSafeInteger(flightPrice + hotelPrice)) return null;
  return { amount: flightPrice + hotelPrice, currency: flight.currency };
}
