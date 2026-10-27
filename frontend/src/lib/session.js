import { TravelApi } from './api.js';
import { componentRequest, currentPlans, isPending, latestChanges, payloadOf, uuid } from './travel.js';
import { agentProposalReady } from './agentProposal.js';
import { restoreHotelPages } from './hotelPages.js';

const storage = {
  get(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } },
  set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* A private browser can still keep the current session in memory. */ } },
};
const origin = typeof location === 'undefined' ? 'test' : location.origin;
const KEY = `otravel.studio.v1:${origin}`;

export function friendlyError(message) {
  if (!message) return 'Что-то пошло не так. Попробуйте ещё раз.';
  if (/liteapi|duffel|serpapi|travelpayouts|ratehawk|hotelbeds|провайдер|backend|api\b|http\s*\d/i.test(message)) return 'Не удалось получить предложения. Попробуйте ещё раз или измените параметры поездки.';
  return message;
}

export class TravelSession {
  constructor(api = new TravelApi(), persistence = storage) {
    this.api = api; this.persistence = persistence;
    const saved = persistence.get(KEY, {});
    this.access = saved.access || null;
    this.deliveries = saved.deliveries || [];
    this.confirmDelivery = saved.confirmDelivery || null;
    this.liveKinds = saved.liveKinds || {};
    this.liveClasses = saved.liveClasses || {};
    this.autoAttempts = saved.autoAttempts || {};
    this.listeners = new Set(); this.runs = new Map(); this.cursor = 0;
    this.state = { snapshot: null, messages: [], plans: [], selectedId: null, changes: [], media: {}, providers: null,
      online: null, restoring: false, sending: false, busy: false, progress: '', notice: null, quote: null,
      previewing: false, confirming: false, orders: [], external: { hotels: null, flights: null }, hotelPagination: { known: false, next: null }, flightClass: ['economy', 'premium_economy', 'business', 'first'].includes(saved.flightClass) ? saved.flightClass : 'economy', autoAttempts: this.autoAttempts, deliveries: this.deliveries, confirmDelivery: this.confirmDelivery };
  }
  getSnapshot = () => this.state;
  subscribe = listener => { this.listeners.add(listener); return () => this.listeners.delete(listener); };
  patch(values) { this.state = { ...this.state, ...values }; this.listeners.forEach(listener => listener()); }
  persist() { this.persistence.set(KEY, { access: this.access, deliveries: this.deliveries, confirmDelivery: this.confirmDelivery, liveKinds: this.liveKinds, liveClasses: this.liveClasses, flightClass: this.state.flightClass, autoAttempts: this.autoAttempts }); }
  journal() { this.persist(); this.patch({ deliveries: [...this.deliveries], confirmDelivery: this.confirmDelivery }); }
  path(suffix = '') { return `/api/v1/sessions/${encodeURIComponent(this.access.session_id)}${suffix}`; }
  request(path, options = {}) { return this.api.request(path, { token: this.access?.session_token, ...options }); }
  notice(text, type = 'error') { this.patch({ notice: { text, type } }); }
  clearNotice() { this.patch({ notice: null }); }

  async initialize() {
    if (this.initializing) return this.initializing;
    this.initializing = (async () => {
      const [health, providers] = await Promise.allSettled([this.api.request('/health', { timeout: 10000 }), this.api.request('/api/v1/providers/status', { timeout: 10000 })]);
      this.patch({ online: health.status === 'fulfilled' && health.value.database === 'ready', providers: providers.status === 'fulfilled' ? providers.value : null });
      if (this.access) {
        this.patch({ restoring: true });
        try { await this.sync(); } catch (error) { this.handleError(error); }
        finally { this.patch({ restoring: false }); }
      }
    })();
    return this.initializing;
  }

  async open({ fresh = false } = {}) {
    if (this.opening) return this.opening;
    this.opening = (async () => {
      if (!fresh && this.access) { await this.sync(); return; }
      const snapshot = await this.api.request('/api/v1/sessions', { method: 'POST', body: {} });
      if (!snapshot.session_id || !snapshot.session_token) throw new Error('Не удалось создать поездку. Попробуйте ещё раз.');
      clearTimeout(this.timer); this.mediaController?.abort(); this.runs.clear(); this.cursor = 0;
      this.access = { session_id: snapshot.session_id, session_token: snapshot.session_token };
      this.deliveries = []; this.confirmDelivery = null; this.liveKinds = {}; this.liveClasses = {}; this.autoAttempts = {}; this.patch({ flightClass: 'economy' }); this.persist();
      this.patch({ snapshot, messages: [], plans: [], selectedId: null, changes: [], media: {}, quote: null, orders: [], busy: false,
        deliveries: [], confirmDelivery: null, external: { hotels: null, flights: null }, hotelPagination: { known: false, next: null }, autoAttempts: {}, notice: null, online: true });
    })();
    try { await this.opening; return true; } catch (error) { this.handleError(error); return false; }
    finally { this.opening = null; }
  }

  async sync() {
    if (!this.access) return;
    if (this.syncing) return this.syncing;
    const sessionId = this.access.session_id;
    this.syncing = (async () => {
      const [snapshot, history, orderData] = await Promise.all([this.request(this.path()), this.request(this.path('/messages')), this.request(this.path('/orders'))]);
      if (this.access?.session_id !== sessionId) return;
      const messages = [...(history.messages || [])].sort((a, b) => a.sequence - b.sequence);
      const ids = [...new Set(messages.map(message => message.run_id).filter(Boolean))];
      const wanted = ids.filter(id => !this.runs.has(id) || isPending(this.runs.get(id)));
      for (let offset = 0; offset < wanted.length; offset += 4) {
        await Promise.all(wanted.slice(offset, offset + 4).map(async id => {
          const run = await this.request(`/api/v1/runs/${encodeURIComponent(id)}`);
          if (this.access?.session_id === sessionId && run.session_id === sessionId) this.runs.set(id, run);
        }));
      }
      if (this.access?.session_id !== sessionId) return;
      if (this.state.snapshot?.session_id === sessionId && this.state.snapshot.state_version > snapshot.state_version) return;
      this.deliveries = this.deliveries.flatMap(entry => {
        const message = messages.find(item => item.client_message_id === entry.body.client_message_id);
        const runId = entry.runId || message?.run_id;
        if (runId && entry.kind === 'live/search') { this.liveKinds[runId] = entry.body.kind; if (entry.body.kind === 'flights') this.liveClasses[runId] = entry.body.request?.cabin_class || 'economy'; }
        if (runId && this.runs.has(runId) && !isPending(this.runs.get(runId))) return [];
        return [{ ...entry, ...(runId ? { runId } : {}) }];
      });
      const active = [...this.runs.values()].filter(isPending);
      const plans = currentPlans(messages, this.runs, snapshot);
      const hotelPages = restoreHotelPages(messages, this.runs, snapshot, this.liveKinds);
      const external = { hotels: hotelPages.offers, flights: null };
      for (const message of [...messages].reverse()) {
        const run = this.runs.get(message.run_id);
        if (run?.status !== 'completed' || run.result?.state_version !== snapshot.state_version) continue;
        const offers = payloadOf(run).live_offers;
        if (!Array.isArray(offers)) continue;
        const kind = this.liveKinds[run.run_id] || (offers[0]?.details?.kind === 'hotel' ? 'hotels' : offers[0]?.details?.kind === 'flight' ? 'flights' : null);
        const flightClass = this.liveClasses[run.run_id] || offers[0]?.details?.search_request?.cabin_class || 'economy';
        if (kind === 'flights' && flightClass !== this.state.flightClass) continue;
        if (kind === 'flights' && external.flights === null) external.flights = offers;
      }
      const selectedId = plans.some(plan => plan.plan_id === this.state.selectedId) ? this.state.selectedId : plans[0]?.plan_id || null;
      const paused = snapshot.waiting_reason === 'user_pause';
      let notice = this.state.notice;
      const lastRunId = [...messages].reverse().find(message => message.run_id && this.runs.has(message.run_id))?.run_id;
      const lastRun = this.runs.get(lastRunId);
      if (lastRun && !this.reported?.has(lastRun.run_id) && !isPending(lastRun) && (lastRun.error || ['conflict', 'interrupted', 'failed'].includes(lastRun.status))) {
        this.reported ||= new Set(); this.reported.add(lastRun.run_id);
        notice = { type: 'error', text: friendlyError(lastRun.error?.message || 'Не удалось закончить поиск. Проверьте параметры поездки.') };
      }
      const orders = orderData.orders || [];
      if (this.confirmDelivery && orders.some(order => order.quote_id === this.confirmDelivery.quote_id)) this.confirmDelivery = null;
      const quote = this.state.quote?.state_version === snapshot.state_version && !active.length && !snapshot.waiting_reason ? this.state.quote : null;
      const media = this.state.snapshot?.state_version === snapshot.state_version && selectedId === this.state.selectedId ? this.state.media : {};
      this.patch({ snapshot, messages, plans, selectedId, media, external, hotelPagination: hotelPages.pagination, changes: latestChanges(messages, this.runs), orders, online: true,
        busy: !paused && active.length > 0, progress: paused ? 'На паузе' : active.length ? 'Обрабатываем ваш запрос…' : '', quote, notice });
      this.journal();
      void this.loadMedia();
      if (active.length || this.deliveries.some(entry => entry.runId)) this.schedule();
    })();
    try { await this.syncing; } finally { this.syncing = null; }
  }

  schedule() {
    clearTimeout(this.timer);
    this.timer = setTimeout(async () => {
      try {
        const events = await this.request(this.path(`/events?after=${this.cursor}`));
        this.cursor = events.next_cursor ?? this.cursor;
        const latest = (events.events || []).filter(event => event.run_id && isPending(this.runs.get(event.run_id))).at(-1);
        const labels = { MODEL_RESPONSE_RECEIVED: 'Уточняем детали поездки…', TOOL_CALL_STARTED: 'Подбираем предложения…', TOOL_CALL_COMPLETED: 'Собираем маршрут…' };
        if (labels[latest?.event_type]) this.patch({ progress: labels[latest.event_type] });
        await this.sync();
      } catch (error) { this.handleError(error); this.schedule(); }
    }, this.state.snapshot?.waiting_reason === 'user_pause' ? 3500 : 1000);
  }

  async deliver(entry) {
    this.patch({ sending: true, quote: null, notice: null });
    try {
      const accepted = await this.request(this.path(`/${entry.kind}`), { method: 'POST', body: entry.body });
      if (!accepted.run_id) throw new Error('Сервер не вернул идентификатор обработки.');
      entry.runId = accepted.run_id;
      if (entry.kind === 'live/search') { this.liveKinds[accepted.run_id] = entry.body.kind; if (entry.body.kind === 'flights') this.liveClasses[accepted.run_id] = entry.body.request?.cabin_class || 'economy'; }
      this.journal();
      try { await this.sync(); } catch (error) { this.handleError(error); }
      this.schedule();
      return true;
    } catch (error) {
      if (error.status >= 400 && error.status < 500) {
        this.deliveries = this.deliveries.filter(item => item !== entry); this.journal();
        if (error.status === 409) await this.sync().catch(() => {});
      }
      this.handleError(error); return false;
    } finally { this.patch({ sending: false }); }
  }

  async submit(kind, values) {
    if (!this.access && !await this.open()) return false;
    const entry = { id: uuid(), kind, body: { client_message_id: uuid(), expected_version: this.state.snapshot.state_version, ...values } };
    this.deliveries.push(entry); this.journal();
    return this.deliver(entry);
  }
  send(text) { return this.submit('messages', { text }); }
  commands(operations, search = false) { return this.submit('commands', { operations, search }); }
  search() { return this.submit('search', { mode: this.state.snapshot.trip.search_policy.mode }); }
  async ensureAutomaticProposal({ retry = false } = {}) {
    const { snapshot, busy, sending, restoring, online, plans } = this.state;
    if (!snapshot || !this.access || busy || sending || restoring || online !== true || snapshot.waiting_reason || !agentProposalReady(snapshot.trip)) return false;
    if (!retry && plans.length) return false;
    const key = `${snapshot.session_id}:${snapshot.state_version}:agent`;
    if (!retry && this.autoAttempts[key]) return false;
    if (this.deliveries.some(entry => entry.kind === 'search' && entry.body.expected_version === snapshot.state_version)) return false;
    this.autoAttempts = Object.fromEntries([...Object.entries(this.autoAttempts), [key, true]].slice(-40));
    this.persist(); this.patch({ autoAttempts: { ...this.autoAttempts } });
    return this.search();
  }
  demo() { return this.submit('demo', {}); }
  liveSearch(kind, provider = 'serpapi') {
    try { return this.submit('live/search', componentRequest(this.state.snapshot?.trip, kind, provider)); }
    catch (error) { this.notice(error.message); return Promise.resolve(false); }
  }
  autoSearchKey(kind) {
    const snapshot = this.state.snapshot;
    return snapshot ? `${snapshot.session_id}:${snapshot.state_version}:${kind}${kind === 'flights' && this.state.flightClass !== 'economy' ? `:${this.state.flightClass}` : ''}` : null;
  }
  setFlightClass(value) {
    if (!['economy', 'premium_economy', 'business', 'first'].includes(value) || value === this.state.flightClass) return;
    this.patch({ flightClass: value, external: { ...this.state.external, flights: null } });
    this.persist();
    if (this.access) void this.sync().catch(error => this.handleError(error));
  }
  async ensureAutomaticSearch(kind, { retry = false } = {}) {
    const { snapshot, busy, sending, restoring, providers, online, external } = this.state;
    if (!snapshot || !this.access || busy || sending || restoring || online !== true || snapshot.waiting_reason === 'user_pause' || !providers?.serpapi?.configured || !providers?.serpapi?.free_tier_confirmed) return false;
    if (!retry && external[kind] !== null) return false;
    const key = this.autoSearchKey(kind);
    if (!retry && this.autoAttempts[key]) return false;
    if (this.deliveries.some(entry => entry.kind === 'live/search' && entry.body.kind === kind && entry.body.expected_version === snapshot.state_version)) return false;
    let request;
    try { request = componentRequest(snapshot.trip, kind, 'serpapi', this.state.flightClass); } catch { return false; }
    this.autoAttempts = Object.fromEntries([...Object.entries(this.autoAttempts), [key, true]].slice(-40));
    this.persist(); this.patch({ autoAttempts: { ...this.autoAttempts } });
    return this.submit('live/search', request);
  }
  async loadMoreHotels() {
    const { snapshot, busy, sending, restoring, online, providers, hotelPagination } = this.state;
    if (!snapshot || !this.access || busy || sending || restoring || online !== true || snapshot.waiting_reason === 'user_pause' || !providers?.serpapi?.configured || !providers?.serpapi?.free_tier_confirmed || !hotelPagination.next) return false;
    if (this.deliveries.some(entry => entry.kind === 'live/search' && entry.body.kind === 'hotels' && entry.body.expected_version === snapshot.state_version)) return false;
    let request;
    try { request = componentRequest(snapshot.trip, 'hotels'); } catch { return false; }
    request.request.next_page_token = hotelPagination.next;
    return this.submit('live/search', request);
  }
  hotelReviews = offerId => this.request(this.path(`/hotel-reviews?offer_id=${encodeURIComponent(offerId)}`), { timeout: 30000 });
  destinationPhotos = cities => this.api.request(`/api/v1/destination-photos?${cities.map(city => `city=${encodeURIComponent(city)}`).join('&')}`, { timeout: 20000 });
  async retryDelivery(id) {
    const entry = this.deliveries.find(item => item.id === id);
    if (!entry) return;
    await this.sync().catch(() => {});
    const refreshed = this.deliveries.find(item => item.id === id);
    if (!refreshed) return true;
    if (refreshed.runId) { this.schedule(); return true; }
    return this.deliver(refreshed);
  }
  async control(action) {
    if (['undo', 'cancel'].includes(action)) return this.submit('control', { action });
    try {
      const snapshot = await this.request(this.path('/control'), { method: 'POST', body: { action } });
      this.patch({ snapshot, quote: null, notice: null }); await this.sync(); this.schedule();
    } catch (error) { this.handleError(error); await this.sync().catch(() => {}); }
  }

  selectPlan(id) { this.patch({ selectedId: id, quote: null }); void this.loadMedia(); }
  async loadMedia() {
    const { selectedId, snapshot } = this.state;
    if (!selectedId || !snapshot) return;
    const key = `${snapshot.session_id}:${snapshot.state_version}:${selectedId}`;
    if (this.mediaKey === key) return;
    this.mediaController?.abort(); this.mediaController = new AbortController(); this.mediaKey = key;
    try {
      const response = await this.request(this.path(`/media?plan_id=${encodeURIComponent(selectedId)}&state_version=${snapshot.state_version}`), { signal: this.mediaController.signal, timeout: 30000 });
      if (response.session_id !== this.access?.session_id || response.state_version !== this.state.snapshot?.state_version || response.plan_id !== this.state.selectedId) return;
      this.patch({ media: Object.fromEntries((response.items || []).map(item => [item.offer_id, item.media])) });
    } catch { /* Optional photos never block a plan or checkout. */ }
  }

  async preview() {
    if (this.confirmDelivery) { this.notice('Сначала проверьте результат предыдущего оформления.', 'info'); return; }
    const { selectedId, snapshot } = this.state;
    this.patch({ previewing: true, quote: null, notice: null });
    try {
      const quote = await this.request(this.path('/checkout/preview'), { method: 'POST', body: { plan_id: selectedId, state_version: snapshot.state_version } });
      if (this.state.snapshot.state_version !== quote.state_version) throw new Error('Поездка изменилась. Проверьте корзину снова.');
      this.patch({ quote });
    } catch (error) { this.handleError(error); await this.sync().catch(() => {}); }
    finally { this.patch({ previewing: false }); }
  }
  closeQuote() { this.patch({ quote: null }); }
  async confirm() {
    const quote = this.state.quote;
    if (!quote || this.state.confirming) return;
    this.confirmDelivery ||= { quote_id: quote.quote_id, quote_version: quote.quote_version, state_version: quote.state_version, idempotency_key: uuid(), confirmed: true };
    this.journal(); return this.retryConfirmation();
  }
  async retryConfirmation() {
    if (!this.confirmDelivery || this.state.confirming) return;
    this.patch({ confirming: true, notice: null });
    try {
      await this.request(this.path('/checkout/confirm'), { method: 'POST', body: this.confirmDelivery });
      this.confirmDelivery = null; this.journal(); this.patch({ quote: null }); await this.sync();
      this.notice('Пример поездки сохранён. Бронирование не создано.', 'success');
    } catch (error) {
      if (error.status >= 400 && error.status < 500) { this.confirmDelivery = null; this.journal(); this.patch({ quote: null }); }
      this.handleError(error); await this.sync().catch(() => {});
    } finally { this.patch({ confirming: false }); }
  }
  handleError(error) {
    if (!error.status) this.patch({ online: false });
    this.notice(friendlyError(error.message || 'Не удалось выполнить действие. Поездка сохранена.'));
  }
  destroy() { clearTimeout(this.timer); this.mediaController?.abort(); }
}

export const session = new TravelSession();
