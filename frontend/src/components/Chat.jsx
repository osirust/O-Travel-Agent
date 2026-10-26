import { useEffect, useRef } from 'react';
import { ChevronRight, Info, LoaderCircle, Pause, Play, RotateCcw, Send, Sparkles, Square, UsersRound, ArrowUpRight } from 'lucide-react';
import { session } from '../lib/session.js';
import { offerKind } from '../lib/travel.js';
import { TravelImage } from './Ui.jsx';

function messageText(message) {
  if (message.kind === 'command') return 'Параметры поездки обновлены';
  if (message.role !== 'assistant') return message.text;
  const text = message.text || '';
  if (/не удалось завершить обработку/i.test(text)) return 'Не удалось найти предложения. Параметры поездки сохранены — попробуйте ещё раз.';
  if (/demo|демо/i.test(text)) return 'Это пример поездки. Цены и наличие мест пока не подтверждены.';
  if (/получены компоненты поставщика/i.test(text)) return 'Варианты готовы. Посмотрите перелёты и отели в соседнем разделе.';
  return text;
}

export default function Chat({ data, draft, setDraft, onSend, onEdit, onSelect }) {
  const list = useRef(null), pinned = useRef(true);
  const paused = data.snapshot?.waiting_reason === 'user_pause';
  const model = data.providers?._runtime?.llm_provider;
  const modelUnavailable = data.providers && data.providers[model]?.configured === false;
  useEffect(() => { if (pinned.current && list.current) list.current.scrollTo({ top: list.current.scrollHeight, behavior: 'smooth' }); }, [data.messages.length, data.progress, data.snapshot?.pending_question]);
  function send(event) { event.preventDefault(); if (draft.trim() && !data.sending && (!data.busy || paused)) onSend(); }
  const plan = data.plans.find(item => item.plan_id === data.selectedId);
  const hotel = plan?.offers?.find(offer => offerKind(offer) === 'hotel');
  return <aside className="chat-panel"><div className="panel-heading"><h2><span className="agent-mark"><Sparkles size={16} /></span>Агент</h2><span className={`agent-status ${paused ? 'paused' : ''}`}><i />{paused ? 'На паузе' : data.busy ? 'Работаем над поездкой' : 'На связи'}</span></div>
    <div className="chat-scroll" ref={list} onScroll={() => { const el = list.current; pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90; }}>
      <div className="welcome-message"><span className="assistant-avatar">O!</span><div><strong>Куда отправимся?</strong><p>Расскажите о поездке, а я помогу собрать маршрут и подобрать варианты.</p></div></div>
      {data.messages.filter(message => ['user', 'assistant'].includes(message.role)).map(message => <div className={`chat-message ${message.role}`} key={message.id || `${message.role}-${message.sequence}`}><span className="message-avatar">{message.role === 'user' ? <UsersRound size={16} /> : 'O!'}</span><div className="message-bubble"><p>{messageText(message)}</p>{message.created_at && <time>{new Date(message.created_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}</time>}</div></div>)}
      {data.snapshot?.waiting_reason === 'clarification' && data.snapshot.pending_question && <div className="pending-question"><Info size={17} /><div><strong>Уточним одну деталь</strong><p>{data.snapshot.pending_question.text || 'Проверьте недостающие параметры поездки.'}</p>{data.snapshot.pending_question.context?.target_selection_required && <div className="quick-replies">{data.snapshot.trip.stays.map(stay => <button key={stay.id} onClick={() => setDraft(stay.city)}>{stay.city}</button>)}</div>}<button className="text-button" onClick={() => onEdit('route')}>Открыть параметры <ChevronRight size={14} /></button></div></div>}
      {plan && <button className="chat-plan-preview" onClick={() => onSelect(data.selectedId)}><TravelImage city={data.snapshot.trip.stays[0]?.city} media={data.media[hotel?.offer_id]} /><span><strong>Варианты поездки готовы</strong><small>{data.snapshot.trip.stays.map(stay => stay.city).join(' → ')}</small></span><ChevronRight size={18} /></button>}
      {data.busy && <div className="chat-progress" role="status"><span className="thinking-dots"><i /><i /><i /></span><span>{data.progress}</span></div>}
      {!data.messages.length && <div className="chat-suggestions"><button onClick={() => setDraft('Хочу из Казани в Стамбул на неделю, едем вдвоём, бюджет 180 тысяч рублей')}>На неделю в Стамбул <ArrowUpRight size={13} /></button><button onClick={() => setDraft('Планируем поездку с детьми в Анталью. Помоги выбрать даты и отель.')}>К морю с детьми <ArrowUpRight size={13} /></button></div>}
    </div>
    {paused && <div className="pause-banner"><Pause size={16} /><span>Дополнения сохраняются. Продолжим по вашей команде.</span><button onClick={() => session.control('resume')}>Продолжить <Play size={12} /></button></div>}
    {modelUnavailable && <div className="limited-mode"><Info size={14} /><span>Пока заполните детали поездки в форме.</span><button onClick={() => onEdit('route')}>Открыть</button></div>}
    <form className="composer" onSubmit={send}><label className="sr-only" htmlFor="trip-message">Сообщение агенту</label><textarea id="trip-message" placeholder="Что изменим в поездке?" maxLength={8000} rows={2} value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(event); } }} /><button type="submit" className="send-button" aria-label="Отправить сообщение" disabled={!draft.trim() || data.sending || (data.busy && !paused)}>{data.sending ? <LoaderCircle size={20} className="spin" /> : <Send size={19} />}</button></form>
    <div className="chat-controls"><button disabled={!data.snapshot} onClick={() => session.control(paused ? 'resume' : 'pause')}>{paused ? <Play size={13} /> : <Pause size={13} />}{paused ? 'Продолжить' : 'Пауза'}</button><button disabled={!data.snapshot || data.sending} onClick={() => session.control('undo')} title="Отменить последнюю правку"><RotateCcw size={14} /><span>Откатить</span></button>{data.busy && <button onClick={() => session.control('cancel')}><Square size={12} />Стоп</button>}</div>
  </aside>;
}
