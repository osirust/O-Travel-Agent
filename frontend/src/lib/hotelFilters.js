export const EMPTY_HOTEL_FILTERS = { ratingFrom: '', ratingTo: '', priceFrom: '', priceTo: '' };

function rating(value) {
  if (value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number <= 5 ? number : NaN;
}

function price(value) {
  if (value === '') return null;
  const text = String(value).trim().replace(',', '.');
  if (!/^\d{1,12}(?:\.\d{1,2})?$/.test(text)) return NaN;
  const [whole, decimal = ''] = text.split('.');
  return Number(whole) * 100 + Number(decimal.padEnd(2, '0'));
}

export function parseHotelFilters(filters) {
  const ratingFrom = rating(filters.ratingFrom), ratingTo = rating(filters.ratingTo);
  const priceFrom = price(filters.priceFrom), priceTo = price(filters.priceTo);
  if ([ratingFrom, ratingTo].some(Number.isNaN)) return { error: 'Оценка должна быть от 0 до 5.' };
  if ([priceFrom, priceTo].some(Number.isNaN)) return { error: 'Укажите цену положительным числом.' };
  if (ratingFrom !== null && ratingTo !== null && ratingFrom > ratingTo) return { error: 'Начальная оценка не может быть больше конечной.' };
  if (priceFrom !== null && priceTo !== null && priceFrom > priceTo) return { error: 'Начальная цена не может быть больше конечной.' };
  return { ratingFrom, ratingTo, priceFrom, priceTo, active: [ratingFrom, ratingTo, priceFrom, priceTo].some(value => value !== null) };
}

export function filterHotels(offers, filters, currency = 'RUB') {
  const bounds = parseHotelFilters(filters);
  if (bounds.error) return { ...bounds, offers: [] };
  const ratingActive = bounds.ratingFrom !== null || bounds.ratingTo !== null;
  const priceActive = bounds.priceFrom !== null || bounds.priceTo !== null;
  const filtered = offers.filter(offer => {
    const rating = offer.details?.provider_data?.rating;
    if (ratingActive && (typeof rating !== 'number' || !Number.isFinite(rating) || rating < 0 || rating > 5 || (bounds.ratingFrom !== null && rating < bounds.ratingFrom) || (bounds.ratingTo !== null && rating > bounds.ratingTo))) return false;
    const amount = offer.total_minor ?? offer.amount_minor;
    const wholeStay = ['INDICATIVE_STAY_TOTAL', 'PROVIDER_ROOMS_STAY_TOTAL'].includes(offer.price_kind);
    if (priceActive && (!Number.isSafeInteger(amount) || offer.currency !== currency || !wholeStay || (bounds.priceFrom !== null && amount < bounds.priceFrom) || (bounds.priceTo !== null && amount > bounds.priceTo))) return false;
    return true;
  });
  return { ...bounds, offers: filtered };
}
