import test from 'node:test';
import assert from 'node:assert/strict';
import { displayPlanOfferName, humanPlanWarnings } from '../src/lib/planWarnings.js';

test('repeated supplier notices are readable and keep the sandbox, availability and connection limits', () => {
  const warnings = [
    'liteapi:1234567890abcdef: статус SANDBOX; supplier booking не создаётся, точные стыковки/инвентарь не гарантируются.',
    'duffel:abcdef1234567890: статус SANDBOX; supplier booking не создаётся, точные стыковки/инвентарь не гарантируются.',
    'Сумма является simulated estimate; DEMO/SANDBOX не подтверждают реальные цены и наличие.',
    'Справочный пересчёт ЦБ на 2026-10-28; банковские комиссии и курс оплаты неизвестны. Это reference estimate для simulated checkout, не цена покупки.',
  ];
  const original = structuredClone(warnings);
  const result = humanPlanWarnings(warnings);
  assert.equal(result.length, 3);
  assert.match(result[0], /SANDBOX/);
  assert.match(result[0], /Бронирование не создаётся/);
  assert.match(result[0], /наличие мест и точность стыковок не подтверждены/);
  assert.match(result[1], /не подтверждают реальные цены/);
  assert.match(result[2], /Банка России на 2026-10-28/);
  assert.match(result[2], /комиссии и курс оплаты не учтены/);
  assert.match(result[2], /не является ценой покупки/);
  assert.doesNotMatch(result.join(' '), /1234567890abcdef|supplier booking|reference estimate|simulated checkout/);
  assert.deepEqual(warnings, original);
});

test('unknown warnings and object messages are preserved instead of being hidden', () => {
  const specific = 'Не подтверждено размещение ребёнка в выбранном номере.';
  assert.deepEqual(humanPlanWarnings([{ message: specific }, 'Нужна проверка позднего заезда.', specific]), [specific, 'Нужна проверка позднего заезда.']);
  assert.deepEqual(humanPlanWarnings(), []);
});

test('test data and indicative data retain different source descriptions', () => {
  const sourceWarning = source => `source:0123456789abcdef: статус ${source}; supplier booking не создаётся, точные стыковки/инвентарь не гарантируются.`;
  const result = humanPlanWarnings(['DEMO', 'SANDBOX', 'LIVE_INDICATIVE', 'LIVE_BOOKABLE'].map(sourceWarning));
  assert.equal(result.length, 4);
  assert.match(result[0], /демонстрационные данные \(DEMO\)/);
  assert.match(result[1], /тестовые тарифы поставщиков \(SANDBOX\)/);
  assert.match(result[2], /ориентировочные поисковые предложения/);
  assert.ok(result.every(text => /Бронирование не создаётся/.test(text)));
});

test('an opaque hotel ID is a neutral city label and never a made-up property name', () => {
  const offer = { name: 'lp8a2f3', description: 'lp8a2f3', details: { kind: 'hotel' }, currency: 'RUB', amount_minor: 7226994 };
  const original = structuredClone(offer);
  assert.equal(displayPlanOfferName(offer, 'Стамбул'), 'Отель в городе «Стамбул»');
  assert.equal(displayPlanOfferName(offer), 'Отель: название уточняется');
  assert.deepEqual(offer, original);
  assert.equal(displayPlanOfferName({ ...offer, details: { kind: 'hotel', hotel_name: 'Agora Guesthouse' } }, 'Стамбул'), 'Agora Guesthouse');
});

test('real hotel names and non-hotel descriptions are not rewritten', () => {
  for (const name of ['LP Hotel Istanbul', '123 Hotel', 'DEMO — Центральный эскиз']) {
    assert.equal(displayPlanOfferName({ name, details: { kind: 'hotel' } }, 'Стамбул'), name);
  }
  assert.equal(displayPlanOfferName({ name: 'lp8a2f3', details: { kind: 'flight' } }, 'Стамбул'), 'lp8a2f3');
});
