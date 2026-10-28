export const FLIGHT_CLASSES = { economy: 'Эконом', premium_economy: 'Премиум-эконом', business: 'Бизнес', first: 'Первый класс' };

export function airlineLogo(url) {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' && parsed.hostname === 'www.gstatic.com' && !parsed.username && !parsed.password && (!parsed.port || parsed.port === '443') && !parsed.search && !parsed.hash && /^\/flights\/airline_logos\/70px\/[A-Za-z0-9]{2,3}\.png$/.test(parsed.pathname) ? url : null;
  } catch { return null; }
}

export function cabinLabel(value) {
  const normalized = String(value || '').toLowerCase().replace(/[ _-]+/g, ' ').trim();
  if (/premium economy/.test(normalized)) return FLIGHT_CLASSES.premium_economy;
  if (/business/.test(normalized)) return FLIGHT_CLASSES.business;
  if (/first/.test(normalized)) return FLIGHT_CLASSES.first;
  if (/economy/.test(normalized)) return FLIGHT_CLASSES.economy;
  return value || '';
}
