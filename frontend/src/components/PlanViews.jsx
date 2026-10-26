import { ArrowDown, ArrowRight, BedDouble, Check, ChevronRight, CircleCheck, Info, MapPin, Plane, TrainFront, Car, RefreshCw, Sparkles, UsersRound } from 'lucide-react';
import { checkoutAllowed, dateLabel, money, offerAmount, offerKind, offerName, offerStayId, partyLabel, readableChange } from '../lib/travel.js';
import { SourceBadge, TravelImage } from './Ui.jsx';

const kindIcon = kind => kind === 'hotel' ? BedDouble : kind === 'transfer' ? Car : kind === 'train' ? TrainFront : Plane;
const kindLabel = kind => ({ hotel: 'Проживание', flight: 'Перелёты', train: 'Поезда', transfer: 'Трансферы' })[kind] || 'Дополнительно';

function priceBreakdown(plan) {
  if (!plan) return [];
  const groups = new Map();
  for (const offer of plan.offers || []) {
    const component = plan.components?.find(item => item.component_id === offer.offer_id);
    const amount = component?.budget_amount_minor ?? offerAmount(offer);
    const currency = component?.budget_currency && component?.budget_amount_minor != null ? component.budget_currency : offer.currency;
    if (!Number.isSafeInteger(amount) || !currency) continue;
    const kind = offerKind(offer), key = `${kind}:${currency}`;
    const entry = groups.get(key) || { key, kind, currency, amount: 0 };
    entry.amount += amount; groups.set(key, entry);
  }
  return [...groups.values()];
}

export function HotelRow({ offer, trip, media }) {
  const stay = trip.stays.find(item => item.id === offerStayId(offer));
  const details = offer.details || {};
  const checkIn = offer.check_in || stay?.check_in, checkOut = offer.check_out || stay?.check_out;
  const nights = checkIn && checkOut ? Math.round((Date.parse(checkOut) - Date.parse(checkIn)) / 86400000) : null;
  const rooms = offer.room_assignments || details.binding?.room_assignments || stay?.rooms || [];
  return <div className="hotel-row"><TravelImage city={offer.city || stay?.city} media={media} /><div className="hotel-row-content"><SourceBadge source={offer.source_type} small /><h3>{offerName(offer).replace(/^DEMO\s*[—·-]?\s*/i, '')}</h3><p className="muted">{offer.city || stay?.city}{nights !== null ? ` · ${nights} ночей` : ''}</p><div className="amenities">{offer.breakfast === true && <span><Check size={13} />Завтрак</span>}{offer.refundable === true && <span><Check size={13} />Возвратный тариф</span>}{rooms.length > 0 && <span><BedDouble size={14} />{rooms.length} ном.</span>}</div>{offer.room_rates?.some(rate => rate.breakfast !== offer.room_rates[0].breakfast || rate.refundable !== offer.room_rates[0].refundable) && <small className="hint">Условия различаются по номерам</small>}<span className="hotel-price">{money(offerAmount(offer), offer.currency)} <small>за проживание</small></span></div></div>;
}

export function PlanList({ data, onSelect, onDetails }) {
  const plans = [...data.plans].sort((a, b) => a.currency === b.currency && Number.isSafeInteger(a.total_minor) && Number.isSafeInteger(b.total_minor) ? a.total_minor - b.total_minor : 0);
  return <div className="plan-list">{plans.map((plan, index) => {
    const selected = plan.plan_id === data.selectedId;
    const hotels = (plan.offers || []).filter(offer => offerKind(offer) === 'hotel');
    return <article key={plan.plan_id} className={`plan-card ${selected ? 'selected' : ''}`}><button className="plan-card-heading" onClick={() => onSelect(plan.plan_id)} aria-pressed={selected}><span className={`plan-radio ${selected ? 'checked' : ''}`} /><div className="plan-title"><span className="eyebrow">ВАРИАНТ {index + 1}</span><h3>{index === 0 ? 'Ваш маршрут' : `Ещё один вариант`}</h3><SourceBadge source={plan.source_type} small /></div><div className="plan-total"><strong>{money(plan.total_minor, plan.currency)}</strong><small>за всю поездку</small></div></button>{selected ? <><div className="hotel-rows">{hotels.map(offer => <HotelRow key={offer.offer_id} offer={offer} trip={data.snapshot.trip} media={data.media[offer.offer_id]} />)}</div><div className="plan-card-bottom"><span><Plane size={15} />{(plan.offers || []).filter(offer => ['flight', 'train'].includes(offerKind(offer))).length} транспортных участков</span><button className="text-button" onClick={() => onDetails(plan)}>Состав и условия <ChevronRight size={16} /></button></div>{plan.fx_reference_only && <p className="fx-note"><Info size={14} />Пересчёт по справочному курсу. Комиссии конвертации не подтверждены.</p>}{plan.blockers?.length > 0 && <p className="form-error">{plan.blockers[0].message}</p>}</> : <div className="plan-collapsed"><span>{hotels.map(offer => data.snapshot.trip.stays.find(stay => stay.id === offerStayId(offer))?.city || offer.city).filter(Boolean).join(' → ')}</span><button className="text-button" onClick={() => onSelect(plan.plan_id)}>Посмотреть <ArrowRight size={15} /></button></div>}</article>;
  })}</div>;
}

export function ChangeSummary({ data }) {
  const plan = data.plans.find(item => item.plan_id === data.selectedId);
  const comparison = plan?.comparison;
  const labels = [...new Set(data.changes.map(change => readableChange(change, data.snapshot?.trip)))];
  if (!labels.length && !comparison) return null;
  return <section className="change-summary"><div className="section-title"><h3><Sparkles size={18} /> Было → Стало</h3><span className="changes-tag">Изменения сохранены</span></div>{comparison && <div className="comparison-prices"><span>{money(comparison.before_total_minor, comparison.currency)}</span><ArrowRight size={22} /><strong>{money(comparison.after_total_minor, comparison.currency)}</strong></div>}<div className="change-pills">{labels.slice(0, 5).map(label => <span key={label}><CircleCheck size={14} />{label}</span>)}</div>{comparison?.source_changed && <p className="hint">Изменился источник данных. Цены требуют нового подтверждения.</p>}{!comparison && <p className="hint">Требования обновлены. Сумма показывается по текущему расчёту.</p>}</section>;
}

export function TripTimeline({ trip }) {
  const ending = trip.route_policy.kind === 'round_trip' ? trip.origin : trip.route_policy.kind === 'open_jaw' ? trip.route_policy.end_city : null;
  return <div className="trip-timeline">{trip.origin && <div className="timeline-point origin-point"><span className="timeline-icon"><Plane size={18} /></span><div><strong>{trip.origin}</strong><small>Отправление{trip.stays[0]?.check_in ? ` · ${dateLabel(trip.stays[0].check_in)}` : ''}</small></div></div>}{trip.stays.map((stay, index) => <div className="timeline-point" key={stay.id}><span className="timeline-number">{index + 1}</span><div><strong>{stay.city || 'Новый город'}</strong><small>{dateLabel(stay.check_in)} — {dateLabel(stay.check_out)}</small><span className="timeline-room"><BedDouble size={12} />{stay.rooms.length} ном.</span></div></div>)}{ending && trip.stays.length > 0 && <div className="timeline-point origin-point"><span className="timeline-icon"><Plane size={18} /></span><div><strong>{ending}</strong><small>Возвращение · {dateLabel(trip.stays.at(-1).check_out)}</small></div></div>}{trip.route_policy.kind === 'undecided' && <p className="hint">Нужно уточнить завершение маршрута</p>}</div>;
}

export function TripSummary({ data, onEdit, onPreview, onSearch, onDetails }) {
  const trip = data.snapshot?.trip;
  const booked = data.snapshot?.status === 'BOOKED_DEMO';
  const plan = data.plans.find(item => item.plan_id === data.selectedId) || (booked ? data.orders.at(-1)?.plan : null);
  const withinBudget = plan && Number.isSafeInteger(plan.total_minor) && trip.budget?.currency === plan.currency ? plan.total_minor <= trip.budget.amount_minor : null;
  return <aside className="trip-panel"><div className="panel-heading"><h2>Моя поездка</h2><button className="icon-button" onClick={() => onEdit('route')} title="Изменить маршрут" aria-label="Изменить маршрут"><MapPin size={18} /></button></div>{trip && (trip.stays.length || trip.origin) ? <><TripTimeline trip={trip} /><button className="party-summary" onClick={() => onEdit('party')}><UsersRound size={18} /><span>{partyLabel(trip.travelers)}</span><ChevronRight size={15} /></button>{trip.groups.length > 0 && <p className="hint">Групп: {trip.groups.length}. Правила отдельного размещения сохранены.</p>}</> : <div className="empty-trip"><div className="empty-route"><span /><i /><span /><i /><span /></div><p>Здесь появятся маршрут,<br />гости и стоимость поездки</p></div>}<div className="cost-breakdown"><h4>Стоимость поездки</h4>{plan ? priceBreakdown(plan).map(row => { const Icon = kindIcon(row.kind); return <div className="cost-row" key={row.key}><span><Icon size={17} />{kindLabel(row.kind)}</span><strong>{money(row.amount, row.currency)}</strong></div>; }) : <p className="muted">Рассчитаем после подбора вариантов</p>}</div><div className="total-row"><strong>Итого</strong><strong>{plan ? money(plan.total_minor, plan.currency) : '—'}</strong></div>{plan?.total_minor == null && plan?.known_subtotal_minor > 0 && <p className="hint">Известная часть: {money(plan.known_subtotal_minor, plan.currency)}</p>}{withinBudget !== null && <div className={`budget-status ${withinBudget ? 'within' : 'over'}`}><Check size={15} />{withinBudget ? 'В бюджете' : 'Превышает бюджет'}</div>}{trip?.budget && <button className="budget-caption" onClick={() => onEdit('budget')}>Бюджет {money(trip.budget.amount_minor, trip.budget.currency)} <ChevronRight size={12} /></button>}<button className="primary-button checkout-button" disabled={!checkoutAllowed(data.snapshot, plan, data.busy || data.sending) || data.previewing || Boolean(data.confirmDelivery)} onClick={onPreview}>{data.previewing ? <RefreshCw size={17} className="spin" /> : null}Проверить корзину <ArrowRight size={17} /></button><div className="checkout-note"><Info size={14} /><p>Тестовое оформление.<br />Реальная бронь не создаётся.</p></div>{plan && <div className="summary-source"><SourceBadge source={plan.source_type} small /><button className="text-button" onClick={() => onDetails(plan)}>Подробнее</button></div>}{trip?.stays.length > 0 && !data.busy && <button className="text-button refresh-search" onClick={onSearch}><RefreshCw size={14} /> Обновить предложения</button>}</aside>;
}

export function PlanDetails({ plan, trip }) {
  return <div className="plan-details"><p className="detail-intro">{partyLabel(trip.travelers)} · {money(plan.total_minor, plan.currency)} <SourceBadge source={plan.source_type} /></p>{(plan.offers || []).map(offer => { const kind = offerKind(offer), Icon = kindIcon(kind), binding = offer.details?.binding || {}; return <section className="detail-offer" key={offer.offer_id}><div className="section-title"><Icon size={20} /><h3>{offerName(offer)}</h3><SourceBadge source={offer.source_type} small /></div><p>{offer.origin && `${offer.origin} → ${offer.destination}`}{offer.departure_date && ` · ${dateLabel(offer.departure_date)}`}</p><strong>{money(offerAmount(offer), offer.currency)}</strong>{offer.price_kind?.includes('NIGHTLY') && <span className="hint"> за ночь; не общая стоимость поездки</span>}{(offer.room_assignments || binding.room_assignments || []).map((room, index) => <p key={room.room_id || room.id || index} className="hint">Номер {index + 1}: {(room.traveler_ids || []).map(id => { const traveler = trip.travelers.find(person => person.id === id); return traveler?.type === 'child' ? `ребёнок ${traveler.age ?? '?'} лет` : 'взрослый'; }).join(', ')}</p>)}{offer.retrieved_at && <p className="hint">Получено {new Date(offer.retrieved_at).toLocaleString('ru-RU')}</p>}</section>; })}{(plan.warnings || []).map((warning, index) => <p className="warning-text" key={index}><Info size={16} />{typeof warning === 'string' ? warning : warning.message}</p>)}{(plan.blockers || plan.violations || []).map((blocker, index) => <p className="form-error" key={index}>{blocker.message}</p>)}</div>;
}
