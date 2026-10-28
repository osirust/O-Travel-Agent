import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError } from '../src/lib/api.js';
import { TravelSession } from '../src/lib/session.js';

const storageKey = 'otravel.studio.v1:test';
const oldAccess = { session_id: 'missing-session', session_token: 'old-test-token' };
const oldDelivery = { id: 'pending-local-message', kind: 'messages', body: { client_message_id: 'old-message', expected_version: 7, text: 'Не повторять в новой поездке' } };
const oldConfirmation = { quote_id: 'old-quote', state_version: 7, idempotency_key: 'old-confirmation', confirmed: true };
const freshSnapshot = () => ({ session_id: 'new-session', session_token: 'new-test-token', state_version: 0, waiting_reason: null, trip: { origin: null, travelers: [], stays: [] } });

function persistenceFor(saved) {
  const entries = new Map([[storageKey, structuredClone(saved)]]);
  return {
    entries,
    get: (key, fallback) => entries.has(key) ? structuredClone(entries.get(key)) : fallback,
    set: (key, value) => entries.set(key, structuredClone(value)),
  };
}

function recoveringApi(calls) {
  return { request: async (path, options = {}) => {
    calls.push({ path, ...structuredClone(options) });
    if (path === '/health') return { database: 'ready' };
    if (path === '/api/v1/providers/status') return {};
    if (path === '/api/v1/sessions' && options.method === 'POST') return freshSnapshot();
    if (path === '/api/v1/sessions/missing-session') throw new ApiError('Сессия не найдена.', 404, 'not_found');
    if (path === '/api/v1/sessions/new-session') return freshSnapshot();
    if (path.endsWith('/messages')) return { messages: [] };
    if (path.endsWith('/orders')) return { orders: [] };
    throw new Error(`Unexpected request: ${path}`);
  } };
}

test('a missing stored session opens a fresh trip without replaying its message or confirmation', async t => {
  const calls = [];
  const persistence = persistenceFor({ access: oldAccess, deliveries: [oldDelivery], confirmDelivery: oldConfirmation, flightClass: 'business', liveKinds: { old: 'flights' }, autoAttempts: { old: true } });
  const session = new TravelSession(recoveringApi(calls), persistence);
  t.after(() => session.destroy());
  session.patch({ busy: true, progress: 'Старое ожидание', quote: { quote_id: 'old-quote' }, previewing: true, confirming: true });

  assert.equal(await session.open(), true);
  assert.deepEqual(session.access, { session_id: 'new-session', session_token: 'new-test-token' });
  assert.equal(session.state.snapshot.session_id, 'new-session');
  assert.equal(session.state.snapshot.state_version, 0);
  assert.deepEqual(session.state.messages, []);
  assert.deepEqual(session.state.deliveries, []);
  assert.equal(session.confirmDelivery, null);
  assert.equal(session.state.confirmDelivery, null);
  assert.equal(session.state.quote, null);
  assert.equal(session.state.busy, false);
  assert.equal(session.state.previewing, false);
  assert.equal(session.state.confirming, false);
  assert.equal(session.state.progress, '');
  assert.equal(session.state.flightClass, 'economy');
  assert.deepEqual(session.liveKinds, {});
  assert.deepEqual(session.autoAttempts, {});
  assert.equal(session.state.notice, null);
  assert.deepEqual(persistence.entries.get(`${storageKey}:unavailable:missing-session`), { access: oldAccess, deliveries: [oldDelivery], confirmDelivery: oldConfirmation });
  assert.deepEqual(calls.filter(call => call.method === 'POST'), [{ path: '/api/v1/sessions', method: 'POST', body: {} }]);
  assert.deepEqual(persistence.entries.get(storageKey).access, session.access);

  await session.sync();
  const freshCalls = calls.filter(call => call.path.startsWith('/api/v1/sessions/new-session'));
  assert.equal(freshCalls.length, 3);
  assert.ok(freshCalls.every(call => call.token === 'new-test-token'));
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
});

test('parallel initialization and opening recover one missing trip exactly once', async t => {
  const calls = [];
  const session = new TravelSession(recoveringApi(calls), persistenceFor({ access: oldAccess, deliveries: [oldDelivery] }));
  t.after(() => session.destroy());
  const [, firstOpen, secondOpen] = await Promise.all([session.initialize(), session.open(), session.open()]);
  assert.equal(firstOpen, true);
  assert.equal(secondOpen, true);
  assert.equal(session.access.session_id, 'new-session');
  assert.equal(session.state.snapshot.session_id, 'new-session');
  assert.equal(session.state.restoring, false);
  assert.equal(session.state.notice, null);
  assert.equal(calls.filter(call => call.path === '/api/v1/sessions' && call.method === 'POST').length, 1);
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
});

for (const failure of [
  { status: 403, code: 'access_denied', message: 'Ресурс принадлежит другой сессии.' },
  { status: 503, code: 'database_unavailable', message: 'Хранилище недоступно.' },
  { status: 0, code: 'network_error', message: 'Не удалось подключиться.' },
]) {
  test(`restoring failure ${failure.status}/${failure.code} preserves the existing trip and pending journal`, async t => {
    const calls = [];
    const persistence = persistenceFor({ access: oldAccess, deliveries: [oldDelivery], confirmDelivery: oldConfirmation });
    const session = new TravelSession({ request: async (path, options = {}) => { calls.push({ path, ...options }); throw new ApiError(failure.message, failure.status, failure.code); } }, persistence);
    t.after(() => session.destroy());
    assert.equal(await session.open(), false);
    assert.deepEqual(session.access, oldAccess);
    assert.deepEqual(session.deliveries, [oldDelivery]);
    assert.deepEqual(session.confirmDelivery, oldConfirmation);
    assert.equal(session.state.snapshot, null);
    assert.ok(session.state.notice);
    assert.ok(!calls.some(call => call.method === 'POST'));
    assert.equal(persistence.entries.has(`${storageKey}:unavailable:missing-session`), false);
  });
}

test('a missing run does not replace an existing session', async t => {
  const calls = [];
  const access = { session_id: 'existing-session', session_token: 'existing-test-token' };
  const persistence = persistenceFor({ access });
  const session = new TravelSession({ request: async (path, options = {}) => {
    calls.push({ path, ...options });
    if (path === '/api/v1/sessions/existing-session') return { ...freshSnapshot(), session_id: access.session_id };
    if (path.endsWith('/messages')) return { messages: [{ role: 'assistant', sequence: 1, run_id: 'missing-run', text: 'Сохранённое сообщение' }] };
    if (path.endsWith('/orders')) return { orders: [] };
    if (path === '/api/v1/runs/missing-run') throw new ApiError('Обработка сообщения не найдена.', 404, 'not_found');
    throw new Error(`Unexpected request: ${path}`);
  } }, persistence);
  t.after(() => session.destroy());
  assert.equal(await session.open(), false);
  assert.deepEqual(session.access, access);
  assert.ok(session.state.notice);
  assert.ok(!calls.some(call => call.method === 'POST'));
  assert.equal(persistence.entries.has(`${storageKey}:unavailable:existing-session`), false);
});
