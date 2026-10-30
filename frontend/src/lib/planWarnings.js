import { offerKind, offerName } from './travel.js';

const sourceWarnings = {
  DEMO: 'В плане есть демонстрационные данные (DEMO). Бронирование не создаётся; наличие мест и точность стыковок не подтверждены.',
  SANDBOX: 'В плане есть тестовые тарифы поставщиков (SANDBOX). Бронирование не создаётся; наличие мест и точность стыковок не подтверждены.',
  LIVE_INDICATIVE: 'В плане есть ориентировочные поисковые предложения. Бронирование не создаётся; наличие мест и точность стыковок требуют подтверждения.',
  LIVE_BOOKABLE: 'Предложения поставщиков требуют проверки перед оформлением. Бронирование не создаётся; наличие мест и точность стыковок не подтверждены.',
};

export function humanPlanWarnings(warnings = []) {
  return [...new Set(warnings.map(warning => {
    const text = typeof warning === 'string' ? warning.trim() : typeof warning?.message === 'string' ? warning.message.trim() : '';
    const source = text.match(/^[^\n]+: статус (DEMO|SANDBOX|LIVE_INDICATIVE|LIVE_BOOKABLE); supplier booking не создаётся, точные стыковки\/инвентарь не гарантируются\.$/);
    if (source) return sourceWarnings[source[1]];
    if (text === 'Сумма является simulated estimate; DEMO/SANDBOX не подтверждают реальные цены и наличие.') {
      return 'Итог — пример расчёта. DEMO и SANDBOX не подтверждают реальные цены и наличие мест.';
    }
    const fx = text.match(/^Справочный пересчёт ЦБ на (\d{4}-\d{2}-\d{2}); банковские комиссии и курс оплаты неизвестны\. Это reference estimate для simulated checkout, не цена покупки\.$/);
    if (fx) return `Пересчёт по справочному курсу Банка России на ${fx[1]}. Банковские комиссии и курс оплаты не учтены; итог не является ценой покупки.`;
    return text;
  }).filter(Boolean))];
}

const opaqueHotelName = value => /^(?:lp[a-f0-9]{5,16}|liteapi:[a-f0-9]{16,64})$/i.test(value);

export function displayPlanOfferName(offer, city) {
  if (offerKind(offer) !== 'hotel') return offerName(offer);
  const names = [offer?.name, offer?.details?.hotel_name, offer?.description]
    .filter(value => typeof value === 'string' && value.trim()).map(value => value.trim());
  const name = names.find(value => !opaqueHotelName(value));
  if (name) return name;
  if (names.some(opaqueHotelName)) return city ? `Отель в городе «${city}»` : 'Отель: название уточняется';
  return offerName(offer);
}
