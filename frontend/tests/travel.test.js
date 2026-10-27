import test from 'node:test';
import assert from 'node:assert/strict';
import { checkoutAllowed, componentRequest, currentPlans, mentionedCity, minorUnits, money, safeHotelMedia } from '../src/lib/travel.js';
import { TravelApi, ApiError } from '../src/lib/api.js';
import { friendlyError, TravelSession } from '../src/lib/session.js';
import { buildTripOperations, createTripDraft } from '../src/lib/tripDraft.js';
import { EMPTY_HOTEL_FILTERS, filterHotels, parseHotelFilters, sortHotels } from '../src/lib/hotelFilters.js';
import { cartTotal, compareToBudget } from '../src/lib/cart.js';
import { airlineLogo, cabinLabel } from '../src/lib/flights.js';
import { agentProposalReady } from '../src/lib/agentProposal.js';
import { restoreHotelPages } from '../src/lib/hotelPages.js';

const emptyTrip = { origin: null, travelers: [], groups: [], stays: [], budget: null, constraints: [], route_policy: { kind: 'round_trip', end_city: null }, search_policy: { mode: 'DEMO', hotel_provider: 'liteapi', flight_provider: 'serpapi', demo_fallback: true, guest_nationality: null } };

test('cart totals only comparable prices for the whole itinerary and stay', () => {
  const flights = { amount_minor: 1500000, currency: 'RUB', price_kind: 'INDICATIVE_ITINERARY_TOTAL' };
  const hotels = { amount_minor: 2500000, currency: 'RUB', price_kind: 'INDICATIVE_STAY_TOTAL' };
  assert.deepEqual(cartTotal({ flights, hotels }), { amount: 4000000, currency: 'RUB' });
  assert.equal(cartTotal({ flights, hotels: { ...hotels, price_kind: 'INDICATIVE_NIGHTLY' } }), null);
  assert.equal(cartTotal({ flights, hotels: { ...hotels, currency: 'USD' } }), null);
  assert.deepEqual(cartTotal({ flights, hotels: null }), { amount: 1500000, currency: 'RUB' });
  assert.deepEqual(cartTotal({ flights: null, hotels }), { amount: 2500000, currency: 'RUB' });
  assert.equal(cartTotal({ flights: null, hotels: null }), null);
  assert.equal(cartTotal({ flights: { ...flights, amount_minor: null }, hotels }), null);
});

test('budget comparison only uses known prices in the same currency', () => {
  const budget = { amount_minor: 100000, currency: 'RUB' };
  assert.equal(compareToBudget({ amount: 100000, currency: 'RUB' }, budget), true);
  assert.equal(compareToBudget({ amount: 100001, currency: 'RUB' }, budget), false);
  assert.equal(compareToBudget({ amount: 1, currency: 'USD' }, budget), null);
  assert.equal(compareToBudget(null, budget), null);
  assert.equal(compareToBudget({ amount: 1, currency: 'RUB' }, null), null);
});

test('airline logos are limited to the expected Google image CDN', () => {
  assert.ok(airlineLogo('https://www.gstatic.com/flights/airline_logos/70px/FZ.png'));
  assert.equal(airlineLogo('https://evil.example/flights/airline_logos/70px/FZ.png'), null);
  assert.equal(airlineLogo('https://www.gstatic.com/flights/airline_logos/70px/FZ.png?tracking=1'), null);
  assert.equal(cabinLabel('Business Class'), 'Бизнес');
  assert.equal(cabinLabel('Premium economy'), 'Премиум-эконом');
});

test('provider errors are explained without implementation jargon', () => {
  assert.match(friendlyError('LiteAPI не подтвердил доступ. Проверьте ключ.'), /Не удалось получить предложения/);
  assert.equal(friendlyError('Укажите даты поездки.'), 'Укажите даты поездки.');
});

test('city suggestions require one explicitly mentioned supported city', () => {
  assert.equal(mentionedCity('Хочу в Анталью. Помоги спланировать поездку.'), 'Анталья');
  assert.equal(mentionedCity('Хочу в Анталья.'), 'Анталья');
  assert.equal(mentionedCity('Из Казани в Анталью'), null);
  assert.equal(mentionedCity('Хочу к морю'), null);
  assert.equal(mentionedCity('Стамбулец'), null);
});

test('hotel filters compare ratings and the whole-stay price without guessing missing values', () => {
  const offers = [
    { offer_id: 'a', currency: 'RUB', price_kind: 'INDICATIVE_STAY_TOTAL', amount_minor: 2000000, details: { provider_data: { rating: 4.5 } } },
    { offer_id: 'b', currency: 'RUB', price_kind: 'INDICATIVE_STAY_TOTAL', amount_minor: 4000000, details: { provider_data: { rating: 3.8 } } },
    { offer_id: 'nightly', currency: 'RUB', price_kind: 'INDICATIVE_NIGHTLY', amount_minor: 200000, details: { provider_data: { rating: 4.8 } } },
    { offer_id: 'unrated', currency: 'RUB', price_kind: 'INDICATIVE_STAY_TOTAL', amount_minor: 1800000, details: { provider_data: {} } },
  ];
  assert.equal(filterHotels(offers, EMPTY_HOTEL_FILTERS).offers.length, 4);
  const bounds = { ...EMPTY_HOTEL_FILTERS, ratingFrom: '4', ratingTo: '5', priceFrom: '10000', priceTo: '30000' };
  assert.deepEqual(filterHotels(offers, bounds).offers.map(offer => offer.offer_id), ['a']);
  assert.ok(parseHotelFilters({ ...bounds, ratingFrom: '5', ratingTo: '2' }).error);
  assert.ok(parseHotelFilters({ ...bounds, priceFrom: '30001', priceTo: '30000' }).error);
});

test('hotel sorting uses comparable full prices and keeps missing values last', () => {
  const offers = [
    { offer_id: 'a', currency: 'RUB', price_kind: 'INDICATIVE_STAY_TOTAL', amount_minor: 300, details: { provider_data: { rating: 4.5, review_count: 10 } } },
    { offer_id: 'b', currency: 'RUB', price_kind: 'INDICATIVE_STAY_TOTAL', amount_minor: 100, details: { provider_data: { rating: 4.1, review_count: 500 } } },
    { offer_id: 'night', currency: 'RUB', price_kind: 'INDICATIVE_NIGHTLY', amount_minor: 1, details: { provider_data: {} } },
  ];
  assert.deepEqual(sortHotels(offers, 'price_asc').map(offer => offer.offer_id), ['b', 'a', 'night']);
  assert.deepEqual(sortHotels(offers, 'price_desc').map(offer => offer.offer_id), ['a', 'b', 'night']);
  assert.deepEqual(sortHotels(offers, 'rating_desc').map(offer => offer.offer_id), ['a', 'b', 'night']);
  assert.deepEqual(sortHotels(offers, 'reviews_desc').map(offer => offer.offer_id), ['b', 'a', 'night']);
  assert.deepEqual(offers.map(offer => offer.offer_id), ['a', 'b', 'night']);
});

test('saving a partial draft does not invent travelers, places or dates', () => {
  const draft = createTripDraft(emptyTrip);
  assert.deepEqual(buildTripOperations(emptyTrip, draft), []);
  draft.budget = '150000';
  assert.deepEqual(buildTripOperations(emptyTrip, draft), [{ type: 'set_budget', value: { amount_minor: 15000000, currency: 'RUB', scope: 'whole_trip' } }]);
});

test('changing a group preserves the other trip details and assigns its complete membership', () => {
  const trip = { ...emptyTrip, origin: 'Казань', travelers: [{ id: 'a', type: 'adult', age: null, birth_date: null, group_id: null }, { id: 'b', type: 'child', age: 8, birth_date: null, group_id: null }] };
  const draft = createTripDraft(trip);
  draft.groups.push({ id: 'family', traveler_ids: [], keep_separate: true });
  draft.travelers.forEach(person => { person.group_id = 'family'; });
  assert.deepEqual(buildTripOperations(trip, draft), [{ type: 'upsert_group', target_id: 'family', value: { traveler_ids: ['a', 'b'], keep_separate: true } }]);
});

test('money preserves exact minor units and does not turn an unknown amount into zero', () => {
  assert.equal(minorUnits('120 000,05'), 12000005);
  assert.match(money(12000005), /120\s000,05/);
  assert.equal(money(null), 'Уточняем стоимость');
  for (const value of ['1.001', 'false', '-5', '0', 'NaN']) assert.throws(() => minorUnits(value));
});

const snapshot = { session_id: 's', trip_id: 't', state_version: 4, status: 'OPTIONS_READY', waiting_reason: null };
const plan = { plan_id: 'p', trip_id: 't', state_version: 4, total_minor: 15000000, currency: 'RUB' };
const run = (id, payload, version = 4) => ({ run_id: id, status: 'completed', result: { state_version: version, result: payload } });

test('hotel pagination restores ordered pages, deduplicates and ignores stale or unrelated results', () => {
  const hotel = id => ({ offer_id: id, details: { kind: 'hotel' } });
  const runs = new Map([
    ['first', run('first', { live_offers: [hotel('a'), hotel('b')], hotel_pagination: { page_token: null, next_page_token: 'next-1' } })],
    ['wrong', run('wrong', { live_offers: [hotel('wrong')], hotel_pagination: { page_token: 'wrong', next_page_token: null } })],
    ['second', run('second', { live_offers: [hotel('b'), hotel('c')], hotel_pagination: { page_token: 'next-1', next_page_token: null } })],
    ['stale', run('stale', { live_offers: [hotel('stale')] }, 3)],
  ]);
  const restored = restoreHotelPages(['first', 'wrong', 'second', 'stale'].map(run_id => ({ run_id })), runs, snapshot, {});
  assert.deepEqual(restored.offers.map(offer => offer.offer_id), ['a', 'b', 'c']);
  assert.deepEqual(restored.pagination, { known: true, next: null });
});

test('a readonly answer preserves the matching plan, but a new empty search clears it', () => {
  const messages = [{ run_id: 'search' }, { run_id: 'answer' }];
  const runs = new Map([['search', run('search', { plans: [plan] })], ['answer', run('answer', { plans_preserved: true })]]);
  assert.deepEqual(currentPlans(messages, runs, snapshot), [plan]);
  runs.set('answer', run('answer', { plans: [] }));
  assert.deepEqual(currentPlans(messages, runs, snapshot), []);
});

test('plans from old revisions and other trips never become actionable', () => {
  const messages = [{ run_id: 'search' }];
  for (const candidate of [{ ...plan, state_version: 3 }, { ...plan, trip_id: 'foreign' }]) {
    const runs = new Map([['search', run('search', { plans: [candidate] })]]);
    assert.deepEqual(currentPlans(messages, runs, snapshot), []);
    assert.equal(checkoutAllowed(snapshot, candidate), false);
  }
  assert.equal(checkoutAllowed(snapshot, plan), true);
  assert.equal(checkoutAllowed({ ...snapshot, waiting_reason: 'user_pause' }, plan), false);
  assert.equal(checkoutAllowed(snapshot, plan, true), false);
  assert.equal(checkoutAllowed(snapshot, { ...plan, total_minor: null }), false);
  assert.equal(checkoutAllowed(snapshot, { ...plan, blockers: [{ code: 'missing_components' }] }), false);
});

test('a property photo requires a validated source and identity flags', () => {
  const media = { kind: 'HOTEL', isActualPropertyPhoto: true, url: 'https://static.cupid.travel/hotels/12.jpg' };
  assert.equal(safeHotelMedia(media), media);
  assert.equal(safeHotelMedia({ ...media, isActualPropertyPhoto: false }), null);
  for (const url of ['javascript:alert(1)', 'https://static.cupid.travel.evil.test/hotels/1.jpg', 'https://user:secret@static.cupid.travel/hotels/1.jpg', 'http://static.cupid.travel/hotels/1.jpg']) assert.equal(safeHotelMedia({ ...media, url }), null);
  assert.ok(safeHotelMedia({ ...media, url: 'https://lh3.googleusercontent.com/gps-cs-s/abc=s287' }));
  assert.equal(safeHotelMedia({ ...media, url: 'https://lh3.googleusercontent.com/abc?api_key=bad' }), null);
});

test('API authorization is request-scoped and HTTP failures are not accepted runs', async () => {
  const calls = [];
  const api = new TravelApi({ fetcher: async (path, options) => { calls.push({ path, options }); return new Response(JSON.stringify({ error: { code: 'stale_state', message: 'Обновите поездку' } }), { status: 409 }); } });
  await assert.rejects(api.request('/api/v1/sessions/s', { token: 'session-only' }), error => error.status === 409 && error.code === 'stale_state');
  assert.equal(calls[0].options.headers.Authorization, 'Bearer session-only');
  await assert.rejects(api.request('/health'));
  assert.equal(calls[1].options.headers.Authorization, undefined);
});

function controller(api) {
  const saved = [];
  const client = new TravelSession(api, { get: () => ({}), set: (key, value) => saved.push(structuredClone(value)) });
  client.access = { session_id: 's', session_token: 'test-session' };
  client.patch({ snapshot }); client.sync = async () => {}; client.schedule = () => {};
  return { client, saved };
}

test('automatic search starts only once for a version and allows an explicit retry', async () => {
  let posts = 0;
  const { client } = controller({ request: async () => { posts++; return { run_id: `run-${posts}` }; } });
  const trip = { origin: 'Казань', budget: { currency: 'RUB' }, travelers: [{ id: 'a', type: 'adult' }], legs: [{ origin: 'Казань', destination: 'Стамбул', departure_date: '2026-11-09' }], stays: [{ city: 'Стамбул', check_in: '2026-11-09', check_out: '2026-11-16', rooms: [{ id: 'r', traveler_ids: ['a'] }] }] };
  client.patch({ snapshot: { ...snapshot, trip }, providers: { serpapi: { configured: true, free_tier_confirmed: true } }, online: true });
  assert.equal(await client.ensureAutomaticSearch('hotels'), true);
  assert.equal(await client.ensureAutomaticSearch('hotels'), false);
  assert.equal(posts, 1);
  client.deliveries = [];
  assert.equal(await client.ensureAutomaticSearch('hotels', { retry: true }), true);
  assert.equal(posts, 2);
  client.patch({ snapshot: { ...snapshot, trip, waiting_reason: 'user_pause' } });
  client.deliveries = [];
  assert.equal(await client.ensureAutomaticSearch('flights'), false);
  assert.equal(posts, 2);
});

test('loading more hotels sends the saved page once and does not repeat a pending request', async () => {
  const calls = [];
  const { client } = controller({ request: async (path, options) => { calls.push(options.body); return { run_id: 'page-run' }; } });
  const trip = { travelers: [{ id: 'a', type: 'adult' }], stays: [{ city: 'Стамбул', check_in: '2026-11-09', check_out: '2026-11-16', rooms: [{ id: 'r', traveler_ids: ['a'] }] }] };
  client.patch({ snapshot: { ...snapshot, trip }, providers: { serpapi: { configured: true, free_tier_confirmed: true } }, online: true, hotelPagination: { known: true, next: 'page-two' } });
  assert.equal(await client.loadMoreHotels(), true);
  assert.equal(calls[0].request.next_page_token, 'page-two');
  assert.equal(await client.loadMoreHotels(), false);
  assert.equal(calls.length, 1);
});

test('changing flight cabin starts a separate search and does not reuse the previous offers', async () => {
  const requests = [];
  const { client } = controller({ request: async (path, options) => { requests.push(options.body); return { run_id: `run-${requests.length}` }; } });
  const trip = { origin: 'Казань', travelers: [{ id: 'a', type: 'adult' }], legs: [{ origin: 'Казань', destination: 'Стамбул', departure_date: '2026-11-09' }], stays: [{ city: 'Стамбул' }] };
  client.patch({ snapshot: { ...snapshot, trip }, providers: { serpapi: { configured: true, free_tier_confirmed: true } }, online: true });
  assert.equal(await client.ensureAutomaticSearch('flights'), true);
  assert.equal(requests[0].request.cabin_class, 'economy');
  client.deliveries = [];
  client.patch({ external: { hotels: null, flights: [{ offer_id: 'old' }] } });
  client.setFlightClass('business');
  assert.equal(client.state.external.flights, null);
  assert.equal(await client.ensureAutomaticSearch('flights'), true);
  assert.equal(requests[1].request.cabin_class, 'business');
  client.deliveries = [];
  assert.equal(await client.ensureAutomaticSearch('flights'), false);
  assert.equal(requests.length, 2);
});

test('a component search proceeds during unrelated clarification but not during an explicit pause', async () => {
  let posts = 0;
  const { client } = controller({ request: async () => { posts++; return { run_id: 'component' }; } });
  const trip = { travelers: [{ id: 'a', type: 'adult' }], stays: [{ city: 'Стамбул', check_in: '2026-11-09', check_out: '2026-11-16', rooms: [{ id: 'r', traveler_ids: ['a'] }] }] };
  client.patch({ snapshot: { ...snapshot, trip, waiting_reason: 'clarification' }, providers: { serpapi: { configured: true, free_tier_confirmed: true } }, online: true });
  assert.equal(await client.ensureAutomaticSearch('hotels'), true);
  assert.equal(posts, 1);
  client.deliveries = [];
  client.patch({ snapshot: { ...snapshot, trip, waiting_reason: 'user_pause' } });
  assert.equal(await client.ensureAutomaticSearch('hotels', { retry: true }), false);
  assert.equal(posts, 1);
});

test('agent proposal requires saved trip details and runs once per revision', async () => {
  const calls = [];
  const { client } = controller({ request: async (path, options) => { calls.push({ path, body: options.body }); return { run_id: 'plan-run' }; } });
  const trip = { origin: 'Казань', travelers: [{ id: 'a', type: 'adult' }], route_policy: { kind: 'round_trip' }, search_policy: { mode: 'DEMO' }, stays: [{ city: 'Стамбул', check_in: '2026-11-09', check_out: '2026-11-16', rooms: [{ traveler_ids: ['a'] }] }] };
  assert.equal(agentProposalReady({ ...trip, stays: [{ ...trip.stays[0], rooms: [] }] }), false);
  assert.equal(agentProposalReady(trip), true);
  client.patch({ snapshot: { ...snapshot, trip }, online: true });
  assert.equal(await client.ensureAutomaticProposal(), true);
  assert.equal(calls[0].path.endsWith('/search'), true);
  assert.equal(calls[0].body.mode, 'DEMO');
  client.deliveries = [];
  assert.equal(await client.ensureAutomaticProposal(), false);
  assert.equal(await client.ensureAutomaticProposal({ retry: true }), true);
  assert.equal(calls.length, 2);
  client.deliveries = [];
  client.patch({ snapshot: { ...snapshot, trip, waiting_reason: 'user_pause' } });
  assert.equal(await client.ensureAutomaticProposal({ retry: true }), false);
});

test('an accepted request remains accepted when reading its result fails', async () => {
  const { client } = controller({ request: async () => ({ run_id: 'accepted-run' }) });
  client.sync = async () => { throw new ApiError('Connection lost'); };
  assert.equal(await client.send('План поездки'), true);
  assert.equal(client.deliveries[0].runId, 'accepted-run');
});

test('retry reads the journal again before resending a previously resolved request', async () => {
  let posts = 0;
  const { client } = controller({ request: async () => { posts++; return { run_id: 'r' }; } });
  client.deliveries = [{ id: 'original', kind: 'messages', body: { client_message_id: 'original-message' } }];
  client.sync = async () => { client.deliveries = []; };
  assert.equal(await client.retryDelivery('original'), true);
  assert.equal(posts, 0);
});

test('a network-unknown message retries exactly the original UUID, text and revision', async () => {
  const calls = [];
  const { client, saved } = controller({ request: async (path, options) => {
    calls.push({ path, body: structuredClone(options.body) });
    if (calls.length === 1) throw new ApiError('Connection lost');
    return { run_id: 'r', status: 'pending' };
  } });
  assert.equal(await client.send('Сократи бюджет на 20 тысяч'), false);
  assert.equal(client.deliveries.length, 1);
  client.patch({ snapshot: { ...snapshot, state_version: 9 } });
  await client.retryDelivery(client.deliveries[0].id);
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(calls[1].body.expected_version, 4);
  assert.equal(saved[0].deliveries[0].body.client_message_id, calls[0].body.client_message_id);
});

test('uncertain checkout retries its consent and idempotency key, not a newer quote', async () => {
  const calls = [];
  const { client } = controller({ request: async (path, options) => {
    calls.push(structuredClone(options.body));
    if (calls.length === 1) throw new ApiError('Connection lost');
    return { order_id: 'order' };
  } });
  client.patch({ quote: { quote_id: 'q', quote_version: 2, state_version: 4, plan } });
  await client.confirm();
  assert.ok(client.confirmDelivery);
  client.patch({ quote: { quote_id: 'different', quote_version: 3, state_version: 5 } });
  await client.retryConfirmation();
  assert.deepEqual(calls[0], calls[1]);
  assert.equal(calls[1].quote_id, 'q');
  assert.equal(calls[1].confirmed, true);
  assert.equal(client.confirmDelivery, null);
});

test('an older snapshot cannot undo a newer control revision', async () => {
  const client = new TravelSession({ request: async path => {
    if (path.endsWith('/messages')) return { messages: [] };
    if (path.endsWith('/orders')) return { orders: [] };
    return snapshot;
  } }, { get: () => ({}), set: () => {} });
  client.access = { session_id: 's', session_token: 'test-session' };
  client.patch({ snapshot: { ...snapshot, state_version: 8, waiting_reason: 'user_pause' } });
  await client.sync();
  assert.equal(client.state.snapshot.state_version, 8);
  assert.equal(client.state.snapshot.waiting_reason, 'user_pause');
  client.destroy();
});

test('an old provider failure is not shown after a newer successful search', async () => {
  const client = new TravelSession({ request: async path => {
    if (path.endsWith('/messages')) return { messages: [{ sequence: 1, run_id: 'failed' }, { sequence: 2, run_id: 'success' }] };
    if (path.endsWith('/orders')) return { orders: [] };
    if (path.endsWith('/failed')) return { run_id: 'failed', session_id: 's', status: 'failed', error: { message: 'Старая ошибка' } };
    if (path.endsWith('/success')) return { run_id: 'success', session_id: 's', status: 'completed', result: { state_version: 4, result: { live_offers: [] } } };
    return snapshot;
  } }, { get: () => ({}), set: () => {} });
  client.access = { session_id: 's', session_token: 'test-session' };
  await client.sync();
  assert.equal(client.state.notice, null);
  client.destroy();
});

test('restoring searches shows only the selected class, including an empty result', async () => {
  const client = new TravelSession({ request: async path => {
    if (path.endsWith('/messages')) return { messages: [{ sequence: 1, run_id: 'business' }, { sequence: 2, run_id: 'economy' }] };
    if (path.endsWith('/orders')) return { orders: [] };
    if (path.endsWith('/business')) return { run_id: 'business', session_id: 's', status: 'completed', result: { state_version: 4, result: { live_offers: [] } } };
    if (path.endsWith('/economy')) return { run_id: 'economy', session_id: 's', status: 'completed', result: { state_version: 4, result: { live_offers: [{ offer_id: 'economy', details: { kind: 'flight' } }] } } };
    return snapshot;
  } }, { get: () => ({ flightClass: 'business', liveKinds: { business: 'flights', economy: 'flights' }, liveClasses: { business: 'business', economy: 'economy' } }), set: () => {} });
  client.access = { session_id: 's', session_token: 'test-session' };
  await client.sync();
  assert.deepEqual(client.state.external.flights, []);
  client.destroy();
});

test('standalone API results never replace the whole-trip plan with their empty plans field', () => {
  const runs = new Map([['search', run('search', { plans: [plan] })], ['components', run('components', { plans: [], live_offers: [] })]]);
  assert.deepEqual(currentPlans([{ run_id: 'search' }, { run_id: 'components' }], runs, snapshot), [plan]);
});

test('component requests preserve itinerary scope and reject unsupported room allocation', () => {
  const trip = { origin: 'Казань', budget: { currency: 'RUB' }, travelers: [{ id: 'a', type: 'adult' }, { id: 'c', type: 'child', age: 7 }], legs: [{ origin: 'Казань', destination: 'Стамбул', departure_date: '2026-11-09' }], stays: [{ city: 'Стамбул', check_in: '2026-11-09', check_out: '2026-11-16', rooms: [{ id: 'room', traveler_ids: ['a', 'c'] }] }] };
  const hotels = componentRequest(trip, 'hotels');
  assert.deepEqual(hotels.request.occupancies, [{ adults: 1, children: [7] }]);
  assert.equal(hotels.demo_fallback, false);
  const flights = componentRequest(trip, 'flights');
  assert.deepEqual(flights.request.slices, [{ origin: 'KZN', destination: 'IST', departure_date: '2026-11-09' }]);
  assert.equal(flights.request.children, 1);
  assert.deepEqual(componentRequest(trip, 'flights', 'duffel').request.passengers, [{ type: 'adult' }, { age: 7 }]);
  assert.equal(componentRequest(trip, 'flights', 'travelpayouts').request.slices.length, 1);
  assert.throws(() => componentRequest(trip, 'hotels', 'liteapi'), /гражданство/i);
  assert.equal(componentRequest({ ...trip, search_policy: { guest_nationality: 'RU' } }, 'hotels', 'liteapi').request.city, 'Стамбул');
  assert.throws(() => componentRequest({ ...trip, stays: [{ ...trip.stays[0], rooms: [{}, {}] }] }, 'hotels'));
});
