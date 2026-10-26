import { useEffect, useState, useSyncExternalStore } from 'react';
import { AlertCircle, CalendarDays, CheckCircle2, Compass, Info, LoaderCircle, MessageCircle, Plane, Plus, RefreshCw, ShieldCheck, UsersRound, Wallet, X } from 'lucide-react';
import { session } from './lib/session.js';
import { dateLabel, money, partyLabel } from './lib/travel.js';
import { Logo, Modal } from './components/Ui.jsx';
import Landing from './components/Landing.jsx';
import Chat from './components/Chat.jsx';
import ExternalCatalog from './components/ExternalCatalog.jsx';
import TripEditor from './components/TripEditor.jsx';
import Checkout from './components/Checkout.jsx';
import { PlanDetails, TripSummary } from './components/PlanViews.jsx';

export default function App() {
  const data = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const [screen, setScreen] = useState(() => location.hash === '#studio' ? 'studio' : 'home');
  const [draft, setDraft] = useState(() => { try { return sessionStorage.getItem('otravel.draft') || ''; } catch { return ''; } });
  const [offerTab, setOfferTab] = useState('flights');
  const [mobileTab, setMobileTab] = useState('agent'), [editor, setEditor] = useState(null), [details, setDetails] = useState(null), [newConfirm, setNewConfirm] = useState(false);
  useEffect(() => {
    void session.initialize();
    const hash = () => setScreen(location.hash === '#studio' ? 'studio' : 'home');
    const focus = () => { if (session.access) session.sync().catch(error => session.handleError(error)); };
    window.addEventListener('hashchange', hash); window.addEventListener('focus', focus);
    return () => { window.removeEventListener('hashchange', hash); window.removeEventListener('focus', focus); };
  }, []);
  useEffect(() => { try { sessionStorage.setItem('otravel.draft', draft); } catch { /* keep the in-memory draft */ } }, [draft]);
  useEffect(() => { if (screen === 'studio') void session.open(); }, [screen]);
  async function start(city) { location.hash = 'studio'; setScreen('studio'); if (city) setDraft(`Хочу в ${city}. Помоги спланировать поездку.`); window.scrollTo(0, 0); await session.open(); }
  function home() { location.hash = ''; setScreen('home'); window.scrollTo(0, 0); }
  const edit = async (tab, suggestedCity = null) => { if (!session.getSnapshot().snapshot && !await session.open()) return; setEditor({ tab: tab || 'route', suggestedCity }); };
  const selected = data.plans.find(plan => plan.plan_id === data.selectedId), trip = data.snapshot?.trip;
  async function send() { if (await session.send(draft.trim())) setDraft(''); }
  async function save(operations, search) { const success = operations.length ? await session.commands(operations, search) : search ? await session.search() : true; if (success && search) setMobileTab('options'); return success; }
  if (screen === 'home') return <Landing onStart={start} hasSession={Boolean(session.access)} />;
  return <div className="studio"><header className="studio-header"><Logo onClick={home} /><div className="parameter-bar">
    <button onClick={() => edit('route')}><Plane size={17} /><span>{trip?.stays.length ? [trip.origin, ...trip.stays.map(stay => stay.city)].filter(Boolean).join(' → ') : 'Куда отправимся?'}</span></button>
    <button onClick={() => edit('route')}><CalendarDays size={17} /><span>{trip?.stays[0]?.check_in ? `${dateLabel(trip.stays[0].check_in)} — ${dateLabel(trip.stays.at(-1).check_out)}` : 'Выбрать даты'}</span></button>
    <button onClick={() => edit('party')}><UsersRound size={17} /><span>{partyLabel(trip?.travelers)}</span></button><button onClick={() => edit('budget')}><Wallet size={17} /><span>{trip?.budget ? money(trip.budget.amount_minor, trip.budget.currency) : 'Бюджет'}</span></button>
  </div><div className="studio-header-actions"><button className="new-trip-button" aria-label="Новая поездка" onClick={() => setNewConfirm(true)}><Plus size={17} /><span>Новая поездка</span></button></div></header>
    {(data.notice || data.online === false || data.restoring) && <div className={`notice-bar ${data.notice?.type || 'info'}`} role={data.notice?.type === 'error' ? 'alert' : 'status'}>{data.restoring ? <LoaderCircle size={17} className="spin" /> : data.notice?.type === 'success' ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}<span>{data.restoring ? 'Восстанавливаем вашу поездку…' : data.notice?.text || 'Не удалось подключиться. Ваше сообщение сохранено.'}</span><button className="text-button" onClick={() => session.access ? session.sync().catch(error => session.handleError(error)) : session.open()}><RefreshCw size={14} />Проверить</button>{data.notice && <button className="icon-button" onClick={() => session.clearNotice()} aria-label="Скрыть уведомление"><X size={15} /></button>}</div>}
    {data.deliveries.some(entry => !entry.runId) && <div className="delivery-bar"><Info size={16} /><span>Не удалось узнать, прошёл ли запрос. Проверьте его результат.</span>{data.deliveries.filter(entry => !entry.runId).map(entry => <button key={entry.id} onClick={() => session.retryDelivery(entry.id)} disabled={data.sending}>Проверить запрос</button>)}</div>}
    {data.confirmDelivery && <div className="delivery-bar"><ShieldCheck size={16} /><span>Проверим, сохранился ли ваш пример поездки.</span><button onClick={() => session.sync().catch(error => session.handleError(error))}>Проверить</button><button onClick={() => session.retryConfirmation()} disabled={data.confirming}>Повторить подтверждение</button></div>}
    <main className={`workspace mobile-${mobileTab}`}><Chat data={data} draft={draft} setDraft={setDraft} onSend={send} onEdit={edit} onSelect={id => { session.selectPlan(id); const plan = data.plans.find(item => item.plan_id === id); if (plan) setDetails(plan); }} /><section className="options-panel"><div className="options-heading"><div><p className="eyebrow">ВАШЕ ПУТЕШЕСТВИЕ</p><h1>Варианты поездки</h1><p className="muted">Найдите перелёт и отель для вашего путешествия</p></div></div>
      {data.orders.length > 0 && <div className="order-success"><CheckCircle2 size={23} /><div><strong>Пример поездки сохранён</strong><p>Бронирование не создано.</p></div></div>}
      <div className="offer-tabs" role="tablist" aria-label="Предложения">{[['flights', 'Перелёты'], ['hotels', 'Отели']].map(([id, title]) => <button key={id} role="tab" aria-selected={offerTab === id} className={offerTab === id ? 'active' : ''} onClick={() => setOfferTab(id)}>{title}</button>)}</div><ExternalCatalog key={offerTab} kind={offerTab} data={data} onSearch={provider => session.liveSearch(offerTab, provider)} onEdit={() => edit('route')} loadReviews={session.hotelReviews} />
      <div className="workspace-bottom-note"><ShieldCheck size={14} />Ваша поездка сохраняется. Цены могут измениться.</div>
    </section><TripSummary data={data} onEdit={edit} onPreview={() => session.preview()} onSearch={() => session.liveSearch(offerTab)} onDetails={setDetails} /></main>
    <nav className="mobile-navigation" aria-label="Разделы поездки">{[['agent', MessageCircle, 'Агент'], ['options', Compass, 'Варианты'], ['trip', Plane, 'Поездка']].map(([id, Icon, title]) => <button key={id} className={mobileTab === id ? 'active' : ''} onClick={() => setMobileTab(id)}><Icon size={20} /><span>{title}</span>{id === 'options' && data.plans.length > 0 && <i />}</button>)}</nav>
    {editor && <TripEditor snapshot={data.snapshot} initialTab={editor.tab} suggestedCity={editor.suggestedCity} onSave={save} onClose={() => setEditor(null)} />}
    {data.quote && <Checkout key={`${data.quote.quote_id}:${data.quote.quote_version}`} quote={data.quote} previousPlan={selected} confirming={data.confirming} onClose={() => { if (!data.confirming) session.closeQuote(); }} onConfirm={() => session.confirm()} />}
    {details && <Modal title="Состав и условия" onClose={() => setDetails(null)} wide><PlanDetails plan={details} trip={trip} /></Modal>}
    {newConfirm && <Modal title="Начнём новую поездку?" subtitle="Откроется новый пустой маршрут. Текущая поездка останется сохранённой, но в этом браузере откроется новая." onClose={() => setNewConfirm(false)}><footer className="modal-footer"><button className="secondary-button" onClick={() => setNewConfirm(false)}>Вернуться</button><button className="primary-button" onClick={async () => { if (await session.open({ fresh: true })) { setNewConfirm(false); setDraft(''); setMobileTab('agent'); } }}>Создать поездку <Plus size={16} /></button></footer></Modal>}
  </div>;
}
