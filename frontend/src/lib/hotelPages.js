import { payloadOf } from './travel.js';

export function restoreHotelPages(messages, runs, snapshot, liveKinds) {
  let offers = null, known = false, next = null;
  let used = new Set();
  const seenRuns = new Set();
  for (const message of messages) {
    const run = runs.get(message.run_id);
    if (run?.status !== 'completed' || run.result?.state_version !== snapshot.state_version) continue;
    if (seenRuns.has(run.run_id)) continue;
    seenRuns.add(run.run_id);
    const payload = payloadOf(run), page = payload.live_offers;
    if (!Array.isArray(page)) continue;
    const kind = liveKinds[run.run_id] || (page[0]?.details?.kind === 'hotel' ? 'hotels' : null);
    if (kind !== 'hotels') continue;
    const pagination = payload.hotel_pagination;
    if (!pagination || typeof pagination !== 'object') { offers = page; known = false; next = null; used = new Set(); continue; }
    const current = pagination.page_token || null;
    if (!current) {
      offers = page; known = true; used = new Set();
    } else {
      if (!known || !next || current !== next || used.has(current)) continue;
      used.add(current);
      const ids = new Set((offers || []).map(offer => offer.offer_id));
      offers = [...(offers || []), ...page.filter(offer => { if (ids.has(offer.offer_id)) return false; ids.add(offer.offer_id); return true; })];
    }
    const candidate = typeof pagination.next_page_token === 'string' && pagination.next_page_token ? pagination.next_page_token : null;
    next = candidate && !used.has(candidate) && candidate !== current ? candidate : null;
  }
  return { offers, pagination: { known, next } };
}
