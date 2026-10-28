import { payloadOf, safeHotelMedia } from './travel.js';

// View-only data is joined to its exact current offer, never written into prices.
export function restoreHotelMedia(messages, runs, snapshot, offers) {
  const current = new Map((offers || []).map(offer => [offer.offer_id, offer]));
  const result = {};
  for (const message of messages) {
    const run = runs.get(message.run_id);
    if (run?.status !== 'completed' || run.session_id !== snapshot.session_id || run.result?.state_version !== snapshot.state_version) continue;
    for (const item of payloadOf(run).live_media?.items || []) {
      const offer = current.get(item.offer_id), media = item.media;
      const property = offer?.details?.provider_data?.property_token;
      if (offer?.provider !== 'serpapi' || typeof property !== 'string' || !property || media?.source !== 'SERPAPI'
        || media.hotelId !== property || media.sourceHotelId !== property || !safeHotelMedia(media)) continue;
      result[item.offer_id] = media;
    }
  }
  return result;
}

export function hotelMediaGallery(media) {
  if (!safeHotelMedia(media)) return [];
  const seen = new Set();
  return [media, ...(Array.isArray(media.gallery) ? media.gallery.map(photo => ({ ...media, url: photo.url, alt: photo.alt || media.alt, caption: photo.caption })) : [])]
    .filter(photo => safeHotelMedia(photo) && !seen.has(photo.url) && seen.add(photo.url)).slice(0, 5);
}

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
