import { useEffect, useState, useSyncExternalStore } from 'react';
import { AlertCircle, ArrowRight, CalendarDays, CheckCircle2, Compass, Info, LoaderCircle, MessageCircle, Plane, Plus, RefreshCw, Settings2, ShieldCheck, UsersRound, Wallet, X } from 'lucide-react';
import { session } from './lib/session.js';
import { dateLabel, money, partyLabel } from './lib/travel.js';
import { Logo, Modal } from './components/Ui.jsx';
import Landing from './components/Landing.jsx';
import Chat from './components/Chat.jsx';
import ExternalOffers from './components/ExternalOffers.jsx';
import TripEditor from './components/TripEditor.jsx';
import Checkout from './components/Checkout.jsx';
import { EmptyPlans, OrderResult, Providers } from './components/WorkspaceStates.jsx';
import { ChangeSummary, PlanDetails, PlanList, TripSummary } from './components/PlanViews.jsx';

export default function App() {
  const data = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const [screen, setScreen] = useState(() => location.hash === '#studio' ? 'studio' : 'home');
  const [draft, setDraft] = useState(() => { try { return sessionStorage.getItem('otravel.draft') || ''; } catch { return ''; } });
  const [offerTab, setOfferTab] = useState('trip');
  const [mobileTab, setMobileTab] = useState('agent'), [editor, setEditor] = useState(null), [details, setDetails] = useState(null), [providersOpen, setProvidersOpen] = useState(false), [demoConfirm, setDemoConfirm] = useState(false), [newConfirm, setNewConfirm] = useState(false);
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
  const edit = async tab => { if (!session.getSnapshot().snapshot && !await session.open()) return; setEditor(tab || 'route'); };
  const selected = data.plans.find(plan => plan.plan_id === data.selectedId), trip = data.snapshot?.trip;
  const focusChat = () => { setMobileTab('agent'); requestAnimationFrame(() => document.getElementById('trip-message')?.focus()); };
  async function send() { if (await session.send(draft.trim())) setDraft(''); }
  async function save(operations, search) { const success = operations.length ? await session.commands(operations, search) : search ? await session.search() : true; if (success && search) setMobileTab('options'); return success; }
  if (screen === 'home') return <Landing onStart={start} hasSession={Boolean(session.access)} />;
  return <div className="studio"><header className="studio-header"><Logo onClick={home} /><div className="parameter-bar">
    <button onClick={() => edit('route')}><Plane size={17} /><span>{trip?.stays.length ? [trip.origin, ...trip.stays.map(stay => stay.city)].filter(Boolean).join(' → ') : 'Куда отправимся?'}</span></button>
    <button onClick={() => edit('route')}><CalendarDays size={17} /><span>{trip?.stays[0]?.check_in ? `${dateLabel(trip.stays[0].check_in)} — ${dateLabel(trip.stays.at(-1).check_out)}` : 'Выбрать даты'}</span></button>
    <button onClick={() => edit('party')}><UsersRound size={17} /><span>{partyLabel(trip?.travelers)}</span></button><button onClick={() => edit('budget')}><Wallet size={17} /><span>{trip?.budget ? money(trip.budget.amount_minor, trip.budget.currency) : 'Бюджет'}</span></button>
  </div><div className="studio-header-actions"><button className="icon-button" onClick={() => setProvidersOpen(true)} title="Источники и подключение" aria-label="Источники и подключение"><Settings2 size={20} /></button><button className="new-trip-button" aria-label="Новая поездка" onClick={() => setNewConfirm(true)}><Plus size={17} /><span>Новая поездка</span></button></div></header>
    {(data.notice || data.online === false || data.restoring) && <div className={`notice-bar ${data.notice?.type || 'info'}`} role={data.notice?.type === 'error' ? 'alert' : 'status'}>{data.restoring ? <LoaderCircle size={17} className="spin" /> : data.notice?.type === 'success' ? <CheckCircle2 size={17} /> : <AlertCircle size={17} />}<span>{data.restoring ? 'Восстанавливаем вашу поездку…' : data.notice?.text || 'Соединение с backend пока недоступно. Черновик сообщения сохранён.'}</span><button className="text-button" onClick={() => session.access ? session.sync().catch(error => session.handleError(error)) : session.open()}><RefreshCw size={14} />Проверить</button>{data.notice && <button className="icon-button" onClick={() => session.clearNotice()} aria-label="Скрыть уведомление"><X size={15} /></button>}</div>}
    {data.deliveries.some(entry => !entry.runId) && <div className="delivery-bar"><Info size={16} /><span>Результат доставки неизвестен. Проверим прежний запрос, не создавая дубль.</span>{data.deliveries.filter(entry => !entry.runId).map(entry => <button key={entry.id} onClick={() => session.retryDelivery(entry.id)} disabled={data.sending}>Проверить / повторить доставку</button>)}</div>}
    {data.confirmDelivery && <div className="delivery-bar"><ShieldCheck size={16} /><span>Нужно проверить исход уже подтверждённого тестового оформления.</span><button onClick={() => session.sync().catch(error => session.handleError(error))}>Проверить результат</button><button onClick={() => session.retryConfirmation()} disabled={data.confirming}>Повторить то же подтверждение</button></div>}
    <main className={`workspace mobile-${mobileTab}`}><Chat data={data} draft={draft} setDraft={setDraft} onSend={send} onEdit={edit} onSelect={id => { session.selectPlan(id); setMobileTab('options'); }} /><section className="options-panel"><div className="options-heading"><div><p className="eyebrow">ВАШЕ ПУТЕШЕСТВИЕ</p><h1>Варианты поездки</h1><p className="muted">{data.plans.length ? 'Весь маршрут, отели и транспорт в одном месте' : 'Соберём маршрут под ваши пожелания'}</p></div><button className="filter-button" onClick={() => edit('sources')}><Settings2 size={16} /><span>{trip?.search_policy?.mode === 'HYBRID' ? 'Поиск через API' : 'Демокаталог'}</span></button></div>
      {data.orders.length > 0 && <div className="order-success"><CheckCircle2 size={23} /><div><strong>Тестовое оформление завершено</strong><p>Реальная бронь не создана. Заказ {data.orders.at(-1).order_id || data.orders.at(-1).id}</p></div></div>}
      <div className="offer-tabs" role="tablist" aria-label="Предложения">{[['trip', 'Вся поездка'], ['hotels', 'Отели'], ['flights', 'Перелёты']].map(([id, title]) => <button key={id} role="tab" aria-selected={offerTab === id} className={offerTab === id ? 'active' : ''} onClick={() => setOfferTab(id)}>{title}</button>)}</div>{offerTab !== 'trip' ? <ExternalOffers kind={offerTab} data={data} onSearch={() => session.liveSearch(offerTab)} onEdit={() => edit('route')} /> : data.plans.length ? <><PlanList data={data} onSelect={id => session.selectPlan(id)} onDetails={setDetails} /><ChangeSummary data={data} /></> : data.snapshot?.status === 'BOOKED_DEMO' && data.orders.at(-1)?.plan ? <OrderResult order={data.orders.at(-1)} onDetails={setDetails} /> : <EmptyPlans busy={data.busy} hasTrip={Boolean(trip?.stays.length)} onEdit={() => edit('route')} onFocus={focusChat} onDemo={() => setDemoConfirm(true)} />}
      <div className="workspace-bottom-note"><ShieldCheck size={14} />Данные поездки сохраняются на сервере. Источник и актуальность указаны у предложений.</div>
    </section><TripSummary data={data} onEdit={edit} onPreview={() => session.preview()} onSearch={() => session.search()} onDetails={setDetails} /></main>
    <nav className="mobile-navigation" aria-label="Разделы поездки">{[['agent', MessageCircle, 'Агент'], ['options', Compass, 'Варианты'], ['trip', Plane, 'Поездка']].map(([id, Icon, title]) => <button key={id} className={mobileTab === id ? 'active' : ''} onClick={() => setMobileTab(id)}><Icon size={20} /><span>{title}</span>{id === 'options' && data.plans.length > 0 && <i />}</button>)}</nav>
    {editor && <TripEditor snapshot={data.snapshot} providers={data.providers} initialTab={editor} onSave={save} onClose={() => setEditor(null)} />}
    {data.quote && <Checkout key={`${data.quote.quote_id}:${data.quote.quote_version}`} quote={data.quote} previousPlan={selected} confirming={data.confirming} onClose={() => { if (!data.confirming) session.closeQuote(); }} onConfirm={() => session.confirm()} />}
    {details && <Modal title="Состав и условия" onClose={() => setDetails(null)} wide><PlanDetails plan={details} trip={trip} /></Modal>}
    {providersOpen && <Modal title="Источники предложений" onClose={() => setProvidersOpen(false)}><Providers data={data} /></Modal>}
    {demoConfirm && <Modal title="Загрузить пример поездки?" subtitle="Текущие параметры будут заменены многогородским DEMO-примером. Цены и наличие вымышлены." onClose={() => setDemoConfirm(false)}><footer className="modal-footer"><button className="secondary-button" onClick={() => setDemoConfirm(false)}>Оставить поездку</button><button className="primary-button" onClick={async () => { setDemoConfirm(false); if (await session.demo()) setMobileTab('options'); }}>Загрузить пример <ArrowRight size={16} /></button></footer></Modal>}
    {newConfirm && <Modal title="Начнём новую поездку?" subtitle="Откроется новый пустой маршрут. Текущий не удаляется из backend, но этот браузер переключится на новую сессию." onClose={() => setNewConfirm(false)}><footer className="modal-footer"><button className="secondary-button" onClick={() => setNewConfirm(false)}>Вернуться</button><button className="primary-button" onClick={async () => { if (await session.open({ fresh: true })) { setNewConfirm(false); setDraft(''); setMobileTab('agent'); } }}>Создать поездку <Plus size={16} /></button></footer></Modal>}
  </div>;
}
