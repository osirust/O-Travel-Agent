import test from 'node:test';
import assert from 'node:assert/strict';
import { messageText, projectMessageRunError } from '../src/lib/chat.js';
import { TravelSession } from '../src/lib/session.js';

const generic = 'Не удалось завершить обработку. Поездка сохранена; доступны формы и явный DEMO-сценарий.';
const assistant = Object.freeze({ role: 'assistant', text: generic, run_id: 'flight-run', sequence: 1 });
const failed = { run_id: 'flight-run', session_id: 'owned', status: 'failed', error: { code: 'NO_MATCHES', message: 'Google Flights не вернул предложений. Отсутствие рейсов не доказано.' } };

test('source failure projection binds the exact failed run and owned session without mutating history', () => {
  const before = JSON.stringify({ assistant, failed });
  const projected = projectMessageRunError(assistant, new Map([['flight-run', failed]]), 'owned');
  assert.match(messageText(projected), /^Google Flights не вернул предложений/u);
  assert.match(messageText(projected), /не доказывает, что рейсов нет/u);
  assert.match(messageText(projected), /Проверьте маршрут и даты/u);
  assert.equal(JSON.stringify({ assistant, failed }), before);
  for (const run of [{ ...failed, run_id: 'other' }, { ...failed, session_id: 'foreign' }, { ...failed, status: 'completed' }]) {
    assert.equal(projectMessageRunError(assistant, new Map([['flight-run', run]]), 'owned'), assistant);
  }
});

test('sync restores the source error after reload and after a later successful hotel search', async () => {
  const canonical = [assistant, Object.freeze({ role: 'assistant', text: 'Получены компоненты поставщика.', run_id: 'hotel-run', sequence: 2 })];
  const before = JSON.stringify(canonical), requests = [];
  const snapshot = { session_id: 'owned', state_version: 1, waiting_reason: null, trip: { origin: null, travelers: [], stays: [] } };
  const api = { request: async (path, options = {}) => {
    requests.push({ path, options });
    if (path.endsWith('/messages')) return { messages: canonical };
    if (path.endsWith('/orders')) return { orders: [] };
    if (path.endsWith('/flight-run')) return failed;
    if (path.endsWith('/hotel-run')) return { run_id: 'hotel-run', session_id: 'owned', status: 'completed', result: { state_version: 1, result: {} } };
    return snapshot;
  } };
  const persistence = { get: () => ({ access: { session_id: 'owned', session_token: 'synthetic-test-only' } }), set: () => {} };
  for (let reload = 0; reload < 2; reload += 1) {
    const session = new TravelSession(api, persistence);
    await session.sync();
    const messages = session.getSnapshot().messages;
    assert.match(messageText(messages[0]), /^Google Flights не вернул предложений/u);
    assert.equal(messageText(messages[1]), 'Варианты готовы. Посмотрите перелёты и отели в соседнем разделе.');
    assert.equal(JSON.stringify(canonical), before);
    assert.equal(messages[1], canonical[1]);
    session.destroy();
  }
  assert.ok(requests.every(request => !request.options.method && !request.options.body));
});

test('other errors and substantive assistant messages retain their previous rendering', () => {
  const fallback = messageText(assistant);
  for (const error of [{ code: 'REQUEST_TIMEOUT', message: failed.error.message }, { code: 'NO_MATCHES', message: 'Google Hotels не вернул предложений.' }]) {
    assert.equal(messageText(projectMessageRunError(assistant, new Map([['flight-run', { ...failed, error }]]), 'owned')), fallback);
  }
  const substantive = { ...assistant, text: 'Отель найден; предложения перелётов пока недоступны.' };
  assert.equal(messageText(projectMessageRunError(substantive, new Map([['flight-run', failed]]), 'owned')), substantive.text);
});
