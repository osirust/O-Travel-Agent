export const CITIES = ['Москва', 'Санкт-Петербург', 'Сочи', 'Казань', 'Стамбул', 'Анталья', 'Дубай'];
export const SOURCE_LABELS = {
  DEMO: 'Демоданные', SANDBOX: 'Тестовые данные', LIVE_INDICATIVE: 'Ориентировочная цена',
  LIVE_BOOKABLE: 'Данные поставщика', MIXED: 'Смешанный расчёт', INCOMPLETE: 'Неполный расчёт',
};
export const isPending = run => ['pending', 'running'].includes(run?.status);
export const payloadOf = run => run?.result?.result || {};
export const offerKind = offer => offer?.kind || offer?.details?.kind || 'other';
export const offerAmount = offer => offer?.total_minor ?? offer?.amount_minor ?? null;
export const offerStayId = offer => offer?.details?.binding?.stay_id || offer?.component_id;
export const offerName = offer => offer?.name || offer?.details?.hotel_name || offer?.description || (offerKind(offer) === 'hotel' ? 'Проживание' : 'Транспорт');
export const uuid = () => crypto.randomUUID();

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

export function componentRequest(trip, kind) {
  const iata = { 'Москва': 'MOW', 'Санкт-Петербург': 'LED', 'Сочи': 'AER', 'Казань': 'KZN', 'Стамбул': 'IST', 'Анталья': 'AYT', 'Дубай': 'DXB' };
  if (!trip?.travelers?.length || !trip.stays?.length) throw new Error('Сначала заполните маршрут, даты и гостей в параметрах поездки.');
  const currency = trip.budget?.currency || 'RUB';
  if (kind === 'flights') {
    if (!trip.legs.length || trip.legs.some(leg => !iata[leg.origin] || !iata[leg.destination] || !leg.departure_date)) throw new Error('Для поиска перелётов нужны известные города и даты каждого участка.');
    return { kind, provider: 'serpapi', demo_fallback: false, request: { currency, slices: trip.legs.map(leg => ({ origin: iata[leg.origin], destination: iata[leg.destination], departure_date: leg.departure_date })), adults: trip.travelers.filter(person => person.type === 'adult').length, children: trip.travelers.filter(person => person.type === 'child').length } };
  }
  const stay = trip.stays[0];
  if (!stay.check_in || !stay.check_out || !stay.city || stay.rooms.length !== 1) throw new Error('Справочный поиск отелей доступен для первой остановки с одним номером. Для нескольких номеров используйте подбор всей поездки.');
  const guests = trip.travelers.filter(person => stay.rooms[0].traveler_ids.includes(person.id));
  if (guests.some(person => person.type === 'child' && person.age == null)) throw new Error('Укажите возраст детей для поиска отелей.');
  return { kind: 'hotels', provider: 'serpapi', demo_fallback: false, request: { q: stay.city, checkin: stay.check_in, checkout: stay.check_out, currency, occupancies: [{ adults: guests.filter(person => person.type === 'adult').length, children: guests.filter(person => person.type === 'child').map(person => person.age) }] } };
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
    const domains = ['static.cupid.travel', 'snaphotelapi.com', 'liteapi-travel-static-data.s3.amazonaws.com'];
    return url.protocol === 'https:' && !url.username && !url.password && (!url.port || url.port === '443') && domains.includes(url.hostname) ? media : null;
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
