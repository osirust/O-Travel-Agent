import test from 'node:test';
import assert from 'node:assert/strict';
import { checkoutAllowed, componentRequest, currentPlans, minorUnits, money, safeHotelMedia } from '../src/lib/travel.js';
import { TravelApi, ApiError } from '../src/lib/api.js';
import { TravelSession } from '../src/lib/session.js';
import { buildTripOperations, createTripDraft } from '../src/lib/tripDraft.js';

const emptyTrip = { origin: null, travelers: [], groups: [], stays: [], budget: null, constraints: [], route_policy: { kind: 'round_trip', end_city: null }, search_policy: { mode: 'DEMO', hotel_provider: 'liteapi', flight_provider: 'serpapi', demo_fallback: true, guest_nationality: null } };

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
