import { useEffect, useRef } from 'react';
import { ChevronRight, Info, LoaderCircle, Pause, Play, Send, Sparkles, Square, UsersRound, ArrowUpRight } from 'lucide-react';
import { session } from '../lib/session.js';
import { mentionedCity, offerKind } from '../lib/travel.js';
import { chatAcceptsText, pendingEditorTarget } from '../lib/pendingEditor.js';
import { TravelImage } from './Ui.jsx';

function messageText(message) {
  if (message.kind === 'command') return 'Параметры поездки обновлены';
  if (message.role !== 'assistant') return message.text;
  const text = message.text || '';
  if (/не удалось завершить обработку/i.test(text)) return 'Не удалось обработать запрос. Параметры поездки сохранены — их можно изменить в форме.';
  if (/demo|демо/i.test(text)) return 'Это пример поездки. Цены и наличие мест пока не подтверждены.';
  if (/получены компоненты поставщика/i.test(text)) return 'Варианты готовы. Посмотрите перелёты и отели в соседнем разделе.';
  return text;
}

export default function Chat({ data, draft, setDraft, onSend, onEdit, onSelect }) {
  const list = useRef(null), pinned = useRef(true);
  const paused = data.snapshot?.waiting_reason === 'user_pause';
  const model = data.providers?._runtime?.llm_provider;
  const modelUnavailable = Boolean(data.providers && data.providers[model]?.configured === false);
  const pendingAnswer = data.snapshot?.waiting_reason === 'clarification' && Boolean(data.snapshot?.pending_question);
  const acceptsText = chatAcceptsText(modelUnavailable, pendingAnswer);
  const pending = data.snapshot?.pending_question;
  function openPending() {
    const target = pendingEditorTarget(pending);
    onEdit(target.tab, null, target.field);
  }
  const lastUserText = [...data.messages].reverse().find(message => message.role === 'user' && message.kind !== 'command')?.text || '';
  const suggestedCity = mentionedCity(draft) || mentionedCity(lastUserText);
  useEffect(() => { if (pinned.current && list.current) list.current.scrollTo({ top: list.current.scrollHeight, behavior: 'smooth' }); }, [data.messages.length, data.progress, data.snapshot?.pending_question]);
  function send(event) { event.preventDefault(); if (!draft.trim() || data.sending || (data.busy && !paused)) return; if (!acceptsText) onEdit('route', mentionedCity(draft)); else onSend(); }
  const plan = data.plans.find(item => item.plan_id === data.selectedId);
  const hotel = plan?.offers?.find(offer => offerKind(offer) === 'hotel');
  return <aside className="chat-panel"><div className="panel-heading"><h2><span className="agent-mark"><Sparkles size={16} /></span>Агент</h2><span className={`agent-status ${paused || modelUnavailable ? 'paused' : ''}`}><i />{paused ? 'На паузе' : data.busy ? 'Работаем над поездкой' : modelUnavailable ? pendingAnswer ? 'Режим уточнений' : 'Чат недоступен' : 'На связи'}</span></div>
    <div className="chat-scroll" ref={list} onScroll={() => { const el = list.current; pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 90; }}>
      <div className="welcome-message"><span className="assistant-avatar">O!</span><div><strong>Куда отправимся?</strong><p>Расскажите о поездке, а я помогу собрать маршрут и подобрать варианты.</p></div></div>
      {data.messages.filter(message => ['user', 'assistant'].includes(message.role)).map(message => <div className={`chat-message ${message.role}`} key={message.id || `${message.role}-${message.sequence}`}><span className="message-avatar">{message.role === 'user' ? <UsersRound size={16} /> : 'O!'}</span><div className="message-bubble"><p>{messageText(message)}</p>{message.created_at && <time>{new Date(message.created_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}</time>}</div></div>)}
      {pendingAnswer && <div className="pending-question"><Info size={17} /><div><strong>Уточним одну деталь</strong><p>{pending.text || 'Проверьте недостающие параметры поездки.'}</p>{pending.context?.target_selection_required && <div className="quick-replies">{data.snapshot.trip.stays.map(stay => <button key={stay.id} onClick={() => setDraft(stay.city)}>{stay.city}</button>)}</div>}<button className="text-button" onClick={openPending}>Открыть параметры <ChevronRight size={14} /></button></div></div>}
      {plan && <button className="chat-plan-preview" onClick={() => onSelect(data.selectedId)}><TravelImage city={data.snapshot.trip.stays[0]?.city} media={data.media[hotel?.offer_id]} /><span><strong>Варианты поездки готовы</strong><small>{data.snapshot.trip.stays.map(stay => stay.city).join(' → ')}</small></span><ChevronRight size={18} /></button>}
      {data.busy && <div className="chat-progress" role="status"><span className="thinking-dots"><i /><i /><i /></span><span>{data.progress}</span></div>}
      {!data.messages.length && <div className="chat-suggestions"><button onClick={() => setDraft('Хочу из Казани в Стамбул на неделю, едем вдвоём, бюджет 180 тысяч рублей')}>На неделю в Стамбул <ArrowUpRight size={13} /></button><button onClick={() => setDraft('Планируем поездку с детьми в Анталью. Помоги выбрать даты и отель.')}>К морю с детьми <ArrowUpRight size={13} /></button></div>}
    </div>
    {paused && <div className="pause-banner"><Pause size={16} /><span>Дополнения сохраняются. Продолжим по вашей команде.</span><button onClick={() => session.control('resume')}>Продолжить <Play size={12} /></button></div>}
    {modelUnavailable && <div className="limited-mode"><Info size={14} /><span>{pendingAnswer ? 'Ответьте на вопрос или заполните данные в форме. Свободный диалог пока недоступен.' : suggestedCity ? `Вы упомянули город: ${suggestedCity}. Можно указать его в форме.` : 'Чат временно недоступен. Заполните детали поездки в форме.'}</span><button onClick={() => pendingAnswer ? openPending() : onEdit('route', suggestedCity)}>Открыть</button></div>}
    <form className="composer" onSubmit={send}><label className="sr-only" htmlFor="trip-message">Сообщение агенту</label><textarea id="trip-message" placeholder={!acceptsText ? 'Напишите город или откройте форму' : 'Напишите ответ агенту'} maxLength={8000} rows={2} value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); send(event); } }} /><button type="submit" className="send-button" aria-label={!acceptsText ? 'Открыть форму поездки' : 'Отправить сообщение'} disabled={!draft.trim() || !data.providers || data.sending || (data.busy && !paused)}>{data.sending ? <LoaderCircle size={20} className="spin" /> : <Send size={19} />}</button></form>
    {data.busy && <div className="chat-controls"><button onClick={() => session.control('cancel')}><Square size={12} />Стоп</button></div>}
  </aside>;
}
