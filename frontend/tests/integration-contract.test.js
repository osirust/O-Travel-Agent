import test from 'node:test';
import assert from 'node:assert/strict';
import { restoreHotelMedia, hotelMediaGallery } from '../src/lib/hotelPages.js';
import { filterHotels, sortHotels, EMPTY_HOTEL_FILTERS } from '../src/lib/hotelFilters.js';
import { componentRequest, serverPlanReviewAction } from '../src/lib/travel.js';
import { createTripDraft, buildTripOperations } from '../src/lib/tripDraft.js';
import { messageText } from '../src/lib/chat.js';

const snapshot = { session_id: 'session-one', state_version: 4 };
const offer = { offer_id: 'offer-one', provider: 'serpapi', amount_minor: 27600, currency: 'USD', price_kind: 'INDICATIVE_STAY_TOTAL', details: { kind: 'hotel', provider_data: { property_token: 'property-one' } } };
const photo = { url: 'https://lh3.googleusercontent.com/grass-cs/a=s600', source: 'SERPAPI', kind: 'HOTEL', isActualPropertyPhoto: true, hotelId: 'property-one', sourceHotelId: 'property-one', gallery: [], metadata: { rating: 4.7, review_count: 82 } };
function run(media, extra = {}) { return { run_id: 'run-one', session_id: snapshot.session_id, status: 'completed', result: { state_version: 4, result: { live_offers: [offer], live_media: { items: [{ offer_id: offer.offer_id, media }] } } }, ...extra }; }

test('local component selections keep the separate whole-trip quote action and its version guards', () => {
  const state = { ...snapshot, trip_id: 'trip-one', waiting_reason: null };
  const plan = { plan_id: 'plan-one', trip_id: state.trip_id, state_version: 4, total_minor: 150000, currency: 'USD', ready_for_simulated_checkout: true, blockers: [] };
  const before = JSON.stringify({ state, plan });
  assert.equal(serverPlanReviewAction(state, plan).disabled, false);
  const selected = serverPlanReviewAction(state, plan, { hasComponentSelection: true });
  assert.equal(selected.disabled, false);
  assert.equal(selected.label, 'Проверить целый план агента');
  assert.match(selected.explanation, /отдельному плану агента/);
  assert.equal(serverPlanReviewAction(state, null, { hasComponentSelection: true }), null);
  for (const invalid of [{ ...plan, state_version: 3 }, { ...plan, ready_for_simulated_checkout: false }, { ...plan, blockers: [{ code: 'partial' }] }]) assert.equal(serverPlanReviewAction(state, invalid, { hasComponentSelection: true }).disabled, true);
  assert.equal(serverPlanReviewAction(state, plan, { hasComponentSelection: true, confirmationPending: true }).disabled, true);
  assert.equal(serverPlanReviewAction({ ...state, waiting_reason: 'user_pause' }, plan, { hasComponentSelection: true }).disabled, true);
  assert.equal(JSON.stringify({ state, plan }), before);
});

test('property photos use the current matching sidecar without mutating an economic offer', () => {
  const before = JSON.stringify(offer);
  const media = restoreHotelMedia([{ run_id: 'run-one' }], new Map([['run-one', run(photo)]]), snapshot, [offer]);
  assert.equal(media[offer.offer_id], photo);
  assert.equal(JSON.stringify(offer), before);
  for (const invalid of [{ ...photo, hotelId: 'another-property' }, { ...photo, sourceHotelId: 'another-property' }, { ...photo, source: 'LITEAPI' }, { ...photo, url: 'https://evil.test/hotel.jpg' }]) {
    assert.deepEqual(restoreHotelMedia([{ run_id: 'run-one' }], new Map([['run-one', run(invalid)]]), snapshot, [offer]), {});
  }
  for (const invalid of [run(photo, { session_id: 'another-session' }), run(photo, { status: 'running' }), run(photo, { result: { state_version: 3, result: run(photo).result.result } })]) {
    assert.deepEqual(restoreHotelMedia([{ run_id: 'run-one' }], new Map([['run-one', invalid]]), snapshot, [offer]), {});
  }
  assert.deepEqual(restoreHotelMedia([{ run_id: 'run-one' }], new Map([['run-one', run(photo)]]), snapshot, []), {});
});

test('the property gallery preserves DTO identity, removes duplicate and unsafe URLs and stays bounded', () => {
  const media = { ...photo, gallery: [{ url: photo.url }, { url: 'https://evil.test/a.jpg' }, ...Array.from({ length: 8 }, (_, i) => ({ url: `https://lh3.googleusercontent.com/grass-cs/${i}=s600`, alt: `Photo ${i}` }))] };
  const gallery = hotelMediaGallery(media);
  assert.equal(gallery.length, 5);
  assert.equal(new Set(gallery.map(item => item.url)).size, 5);
  assert.ok(gallery.every(item => item.hotelId === photo.hotelId && item.source === 'SERPAPI'));
  assert.deepEqual(hotelMediaGallery(null), []);
  assert.deepEqual(hotelMediaGallery({ ...media, isActualPropertyPhoto: false }), []);
});

test('rating filters and sorting use view metadata while amounts stay in original minor units', () => {
  const offers = [offer, { ...offer, offer_id: 'second' }];
  const media = { [offer.offer_id]: photo, second: { ...photo, metadata: { rating: 3.8, review_count: 100 } } };
  const before = JSON.stringify(offers);
  const filtered = filterHotels(offers, { ...EMPTY_HOTEL_FILTERS, ratingFrom: '4' }, 'USD', media);
  assert.deepEqual(filtered.offers.map(item => item.offer_id), ['offer-one']);
  assert.deepEqual(sortHotels(offers, 'reviews_desc', 'USD', media).map(item => item.offer_id), ['second', 'offer-one']);
  assert.equal(JSON.stringify(offers), before);
});

test('substantive replies mentioning DEMO and provider components are preserved verbatim', () => {
  for (const text of ['Сейчас 1 взрослый и ребёнок 8 лет. Данные DEMO, цена не подтверждена.', 'Да, план демонстрационный. Тестовую корзину можно открыть без покупки.', 'Получены компоненты поставщика. Отель в Белграде, перелёт из Москвы; ещё нужен трансфер.']) {
    assert.equal(messageText({ role: 'assistant', text }), text);
  }
  assert.equal(messageText({ role: 'assistant', text: 'Подобрано DEMO-планов: 3. Все цены и наличие вымышлены; бронь не создана.' }), 'Это пример поездки. Цены и наличие мест пока не подтверждены.');
});

test('editing dates preserves the exact dynamic city reference, changing the city does not reuse it', () => {
  const trip = { origin: 'Москва', travelers: [], groups: [], stays: [{ id: 'stay', city: 'Белград', place_id: 'geo:openmeteo:792680', check_in: '2026-11-10', check_out: '2026-11-13', rooms: [], hotel_id: null }], budget: null, route_policy: { kind: 'round_trip' }, search_policy: { mode: 'DEMO', hotel_provider: 'liteapi', flight_provider: 'serpapi', demo_fallback: true, guest_nationality: null } };
  const draft = createTripDraft(trip);
  assert.deepEqual(buildTripOperations(trip, draft), []);
  draft.stays[0].check_out = '2026-11-14';
  assert.equal(buildTripOperations(trip, draft)[0].value.place_id, trip.stays[0].place_id);
  draft.stays[0].city = 'Стамбул';
  assert.equal(Object.hasOwn(buildTripOperations(trip, draft)[0].value, 'place_id'), false);
});

test('dynamic city flight search uses a country-matched persisted airport binding', () => {
  const trip = { travelers: [{ id: 'adult', type: 'adult' }], stays: [{ city: 'Белград' }], legs: [{ origin: 'Москва', destination: 'Белград', destination_place_id: 'geo:openmeteo:792680', departure_date: '2026-11-10' }], location_refs: { 'geo:openmeteo:792680': { place_id: 'geo:openmeteo:792680', canonical_name: 'Белград', country_iso2: 'RS', provider_bindings: { duffel: { country_iso2: 'RS', iata_code: 'BEG' } } } } };
  assert.deepEqual(componentRequest(trip, 'flights').request.slices, [{ origin: 'MOW', destination: 'BEG', departure_date: '2026-11-10' }]);
  const legacy = structuredClone(trip);
  delete legacy.legs[0].destination_place_id;
  assert.equal(componentRequest(legacy, 'flights').request.slices[0].destination, 'BEG');
  const invalid = structuredClone(trip);
  invalid.location_refs['geo:openmeteo:792680'].provider_bindings.duffel.country_iso2 = 'RU';
  assert.throws(() => componentRequest(invalid, 'flights'), /подтверждённые аэропорты/);
});
