import { ArrowRight, ChevronRight, Info, LoaderCircle, Plane, Sparkles } from 'lucide-react';
import { agentProposalReady } from '../lib/agentProposal.js';
import { money, offerKind } from '../lib/travel.js';
import { HotelRow } from './PlanViews.jsx';
import { SourceBadge } from './Ui.jsx';
import '../agent-proposal.css';

export default function AgentProposal({ data, onDetails, onBuild, onEdit }) {
  const trip = data.snapshot?.trip;
  const ready = agentProposalReady(trip);
  const attemptKey = data.snapshot ? `${data.snapshot.session_id}:${data.snapshot.state_version}:agent` : null;
  const attempted = Boolean(attemptKey && data.autoAttempts?.[attemptKey]);
  const unknown = data.deliveries.some(entry => entry.kind === 'search' && !entry.runId && entry.body.expected_version === data.snapshot?.state_version);
  const waiting = Boolean(data.snapshot?.waiting_reason);
  const loading = data.restoring || data.busy || data.sending || (!attempted && ready && !waiting && data.online);
  const unavailable = data.online === false;
  return <section className="agent-proposals">
    <div className="external-intro"><span className="external-icon"><Sparkles size={24} /></span><div><h2>Предложение от агента</h2><p>Варианты на основе ваших пожеланий и параметров поездки</p></div></div>
    <div className="reference-notice"><Info size={16} /><p>Варианты учитывают сохранённые параметры. Источник и условия каждого предложения указаны отдельно.</p></div>
    {data.plans.length ? <div className="agent-plan-list">{data.plans.map((plan, index) => {
      const hotels = (plan.offers || []).filter(offer => offerKind(offer) === 'hotel');
      const flights = (plan.offers || []).filter(offer => offerKind(offer) === 'flight');
      return <article className="agent-plan" key={plan.plan_id}>
        <div className="agent-plan-heading"><div><span className="eyebrow">ВАРИАНТ {index + 1}</span><h3>{index === 0 ? 'Ваша поездка' : 'Ещё один вариант'}</h3><SourceBadge source={plan.source_type} small /></div><div className="agent-plan-price"><strong>{money(plan.total_minor, plan.currency)}</strong><small>{Number.isSafeInteger(plan.total_minor) ? 'за всю поездку' : 'полная стоимость уточняется'}</small></div></div>
        {plan.source_type === 'DEMO' && <p className="agent-example-notice">Пример поездки: отели, перелёты и цены вымышлены.</p>}
        {plan.source_type === 'MIXED' && <p className="agent-example-notice">В варианте есть тестовые предложения; проверьте источник каждого компонента.</p>}
        {hotels.length ? <div className="agent-hotel-list">{hotels.map(offer => <button className="agent-hotel-button" key={offer.offer_id} onClick={() => onDetails(plan)} aria-label={`Посмотреть отель и перелёты: ${offer.name || offer.description || 'предложение'}`}><HotelRow offer={offer} trip={trip} media={data.selectedId === plan.plan_id ? data.media[offer.offer_id] : null} /><ChevronRight size={20} className="agent-hotel-arrow" /></button>)}</div> : <p className="agent-plan-empty">Проживание в этом варианте пока не подобрано.</p>}
        <div className="agent-plan-footer"><span><Plane size={15} />{flights.length ? `Перелётов в маршруте: ${flights.length}` : 'Перелёты уточняются'}</span><button className="text-button" onClick={() => onDetails(plan)}>Посмотреть состав <ArrowRight size={15} /></button></div>
        {plan.blockers?.length > 0 && <p className="agent-plan-warning">Не все условия поездки подтверждены. Откройте состав для подробностей.</p>}
      </article>;
    })}</div> : <div className="external-empty">{loading ? <LoaderCircle className="spin" size={32} /> : <Sparkles size={32} strokeWidth={1.3} />}<p>{!data.snapshot || data.restoring ? 'Загружаем вашу поездку…' : data.snapshot.waiting_reason === 'user_pause' ? 'Поиск на паузе. Продолжите поездку, чтобы получить предложение.' : waiting ? 'Уточните оставшиеся детали поездки, чтобы агент собрал предложение.' : unavailable ? 'Не удалось подключиться. Попробуйте позже.' : !ready ? 'Расскажите о поездке или заполните маршрут, даты и размещение — агент соберёт варианты.' : unknown ? 'Проверяем результат предыдущего запроса.' : loading ? 'Агент собирает предложение…' : 'Не удалось подобрать предложение. Можно попробовать ещё раз.'}</p>{!ready && !loading && <button className="secondary-button" onClick={onEdit}>Заполнить поездку</button>}{ready && attempted && !unknown && !loading && !waiting && !unavailable && <button className="secondary-button" onClick={onBuild}>Попробовать снова</button>}</div>}
  </section>;
}
