export const CITIES = ['Москва', 'Санкт-Петербург', 'Сочи', 'Казань', 'Стамбул', 'Анталья', 'Дубай'];
export const SOURCE_LABELS = {
  DEMO: 'Пример расчёта', SANDBOX: 'Пример расчёта', LIVE_INDICATIVE: 'Цена ориентировочная',
  LIVE_BOOKABLE: 'Цена предложения', MIXED: 'Расчёт с примерами', INCOMPLETE: 'Расчёт не завершён',
};
export const isPending = run => ['pending', 'running'].includes(run?.status);
export const payloadOf = run => run?.result?.result || {};
export const offerKind = offer => offer?.kind || offer?.details?.kind || 'other';
export const offerAmount = offer => offer?.total_minor ?? offer?.amount_minor ?? null;
export const offerStayId = offer => offer?.details?.binding?.stay_id || offer?.component_id;
export const offerName = offer => offer?.name || offer?.details?.hotel_name || offer?.description || (offerKind(offer) === 'hotel' ? 'Проживание' : 'Транспорт');
export const uuid = () => crypto.randomUUID();

// Only offer a suggestion when one supported city is explicitly named.
// This never changes the itinerary; the traveler chooses what to do in the form.
export function mentionedCity(text) {
  if (typeof text !== 'string') return null;
  const cities = [
    ['Москва', /(?:^|[^а-яё])москв(?:а|у|е|ы|ой|ою)?(?=$|[^а-яё])/iu],
    ['Санкт-Петербург', /(?:^|[^а-яё])(?:санкт[ -]?петербург(?:а|е|у)?|питер(?:а|е|у)?)(?=$|[^а-яё])/iu],
    ['Сочи', /(?:^|[^а-яё])сочи(?=$|[^а-яё])/iu],
    ['Казань', /(?:^|[^а-яё])казан(?:ь|и|ью)(?=$|[^а-яё])/iu],
    ['Стамбул', /(?:^|[^а-яё])стамбул(?:а|е|у)?(?=$|[^а-яё])/iu],
    ['Анталья', /(?:^|[^а-яё])анталь(?:я|ю|е|и|ей)(?=$|[^а-яё])/iu],
    ['Дубай', /(?:^|[^а-яё])дуба(?:й|я|е|ю)(?=$|[^а-яё])/iu],
  ];
  const matches = cities.filter(([, pattern]) => pattern.test(text));
  return matches.length === 1 ? matches[0][0] : null;
}

export function money(amount, currency = 'RUB') {
  if (!Number.isSafeInteger(amount)) return 'Уточняем стоимость';
  try {
    return new Intl.NumberFormat('ru-RU', { style: 'currency', currency, minimumFractionDigits: amount % 100 ? 2 : 0, maximumFractionDigits: 2 }).format(amount / 100);
  } catch { return `${(amount / 100).toFixed(2)} ${currency}`; }
}

export function minorUnits(value) {
  const cleaned = String(value).replace(/\s/g, '').replace(',', '.');
  if (!/^\d+(?:\.\d{1,2})?$/.test(cleaned)) throw new Error('Укажите сумму с точностью до копеек.');
  const [whole, fraction = ''] = cleaned.split('.');
  const result = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isSafeInteger(result) || result <= 0) throw new Error('Укажите положительный бюджет.');
  return result;
}

export function dateLabel(value, options = { day: 'numeric', month: 'short' }) {
  if (!value) return 'Выберите дату';
  const parsed = new Date(`${value.slice(0, 10)}T12:00:00`);
  return Number.isNaN(parsed.valueOf()) ? 'Уточните дату' : new Intl.DateTimeFormat('ru-RU', options).format(parsed);
}

export function dateAfter(days) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

export function partyLabel(travelers = []) {
  const adults = travelers.filter(p => p.type === 'adult').length;
  const children = travelers.length - adults;
  const childLabel = children % 10 === 1 && children % 100 !== 11 ? 'ребёнок' : children % 10 >= 2 && children % 10 <= 4 && (children % 100 < 12 || children % 100 > 14) ? 'ребёнка' : 'детей';
  return travelers.length ? `${adults} взр.${children ? ` · ${children} ${childLabel}` : ''}` : 'Кто поедет?';
}

export function destinationImage(city) {
  const asset = { 'Стамбул': 'istanbul', 'Анталья': 'antalya', 'Сочи': 'sochi' }[city];
  return asset ? `/images/${asset}.png` : null;
}

export function currentPlans(messages, runs, snapshot) {
  if (!snapshot) return [];
  const seen = new Set();
  for (const message of [...messages].reverse()) {
    if (!message.run_id || seen.has(message.run_id)) continue;
    seen.add(message.run_id);
    const run = runs.get(message.run_id);
    if (run?.status !== 'completed' || run.result?.state_version !== snapshot.state_version) continue;
    const payload = payloadOf(run);
    if (Object.hasOwn(payload, 'live_offers')) continue;
    if (Object.hasOwn(payload, 'plans')) {
      return (Array.isArray(payload.plans) ? payload.plans : []).filter(plan => plan.state_version === snapshot.state_version && plan.trip_id === snapshot.trip_id);
    }
  }
  return [];
}

export function componentRequest(trip, kind, provider = 'serpapi', cabinClass = 'economy') {
  const iata = { 'Москва': 'MOW', 'Санкт-Петербург': 'LED', 'Сочи': 'AER', 'Казань': 'KZN', 'Стамбул': 'IST', 'Анталья': 'AYT', 'Дубай': 'DXB' };
  if (!trip?.travelers?.length || !trip.stays?.length) throw new Error('Сначала заполните маршрут, даты и гостей в параметрах поездки.');
  const currency = trip.budget?.currency || 'RUB';
  if (kind === 'flights') {
    if (!Array.isArray(trip.legs) || !trip.legs.length || trip.legs.some(leg => !iata[leg.origin] || !iata[leg.destination] || !leg.departure_date)) throw new Error('Для поиска перелётов укажите города и даты поездки.');
    if (!trip.travelers.some(person => person.type === 'adult')) throw new Error('Добавьте хотя бы одного взрослого путешественника.');
    const slices = trip.legs.map(leg => ({ origin: iata[leg.origin], destination: iata[leg.destination], departure_date: leg.departure_date }));
    if (provider === 'travelpayouts') return { kind, provider, demo_fallback: false, request: { currency, slices: slices.slice(0, 1) } };
    if (provider === 'duffel') {
      const passengers = trip.travelers.map(person => person.type === 'adult' ? { type: 'adult' } : { age: person.age });
      if (passengers.some(person => person.age === null || person.age === undefined && !person.type)) throw new Error('Укажите возраст детей для поиска перелётов Duffel.');
      return { kind, provider, demo_fallback: false, request: { currency, slices, passengers } };
    }
    if (!['economy', 'premium_economy', 'business', 'first'].includes(cabinClass)) throw new Error('Выберите класс обслуживания.');
    return { kind, provider: 'serpapi', demo_fallback: false, request: { currency, slices, adults: trip.travelers.filter(person => person.type === 'adult').length, children: trip.travelers.filter(person => person.type === 'child').length, cabin_class: cabinClass } };
  }
  const stay = trip.stays[0];
  if (!stay.check_in || !stay.check_out || !stay.city || !stay.rooms.length || (provider === 'serpapi' && stay.rooms.length !== 1)) throw new Error('Укажите город, даты и гостей. Пока можно искать отели только для одного номера.');
  const guests = trip.travelers.filter(person => stay.rooms.some(room => room.traveler_ids.includes(person.id)));
  if (guests.some(person => person.type === 'child' && person.age == null)) throw new Error('Укажите возраст детей для поиска отелей.');
  const occupancies = stay.rooms.map(room => { const people = guests.filter(person => room.traveler_ids.includes(person.id)); return { adults: people.filter(person => person.type === 'adult').length, children: people.filter(person => person.type === 'child').map(person => person.age) }; });
  if (occupancies.some(room => room.adults < 1)) throw new Error('Укажите хотя бы одного взрослого в каждом номере.');
  if (provider === 'liteapi') {
    if (!trip.search_policy?.guest_nationality) throw new Error('Укажите гражданство гостей во вкладке «Источники».');
    return { kind: 'hotels', provider, demo_fallback: false, request: { city: stay.city, checkin: stay.check_in, checkout: stay.check_out, currency, occupancies, guestNationality: trip.search_policy.guest_nationality } };
  }
  return { kind: 'hotels', provider: 'serpapi', demo_fallback: false, request: { q: stay.city, checkin: stay.check_in, checkout: stay.check_out, currency, occupancies } };
}

export function latestChanges(messages, runs) {
  for (const message of [...messages].reverse()) {
    const run = runs.get(message.run_id);
    if (run?.status === 'completed' && run.result?.changes?.length) return run.result.changes;
  }
  return [];
}

export function checkoutAllowed(snapshot, plan, busy = false) {
  return Boolean(snapshot && plan && !busy && !snapshot.waiting_reason && snapshot.status !== 'RECOVERABLE_ERROR'
    && plan.state_version === snapshot.state_version && plan.trip_id === snapshot.trip_id
    && plan.ready_for_simulated_checkout !== false && !(plan.blockers || plan.violations || []).length
    && Number.isSafeInteger(plan.total_minor));
}

export function safeHotelMedia(media) {
  if (!media?.isActualPropertyPhoto || media.kind !== 'HOTEL' || !media.url) return null;
  try {
    const url = new URL(media.url);
    const domains = ['static.cupid.travel', 'snaphotelapi.com', 'liteapi-travel-static-data.s3.amazonaws.com', 'lh3.googleusercontent.com', 'lh4.googleusercontent.com', 'lh5.googleusercontent.com', 'lh6.googleusercontent.com'];
    const google = /^lh[3-6]\.googleusercontent\.com$/.test(url.hostname);
    const allowedPath = !google || (!url.search && !url.hash && /^\/[A-Za-z0-9_./=-]+$/.test(url.pathname));
    return url.protocol === 'https:' && !url.username && !url.password && (!url.port || url.port === '443') && domains.includes(url.hostname) && allowedPath ? media : null;
  } catch { return null; }
}

export function readableChange(change, trip) {
  const labels = { budget: 'Бюджет', origin: 'Город отправления', travelers: 'Состав поездки', stays: 'Маршрут и даты', constraints: 'Условия', preferences: 'Пожелания', groups: 'Группы', route_policy: 'Возвращение', search_policy: 'Источники поиска', legs: 'Транспорт' };
  const base = String(change.field || '').split(/[.\[]/)[0];
  const label = labels[base] || 'Параметры поездки';
  if (base === 'budget' && trip?.budget) return `${label}: ${money(trip.budget.amount_minor, trip.budget.currency)}`;
  if (base === 'origin') return `${label}: ${trip?.origin || 'уточняем'}`;
  return label;
}
