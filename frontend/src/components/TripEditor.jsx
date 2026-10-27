import { useState } from 'react';
import { ArrowRight, LoaderCircle, Plus, Trash2 } from 'lucide-react';
import { CITIES, uuid } from '../lib/travel.js';
import { buildTripOperations, createTripDraft } from '../lib/tripDraft.js';
import { COUNTRIES } from '../lib/countries.js';
import { Modal } from './Ui.jsx';

export default function TripEditor({ snapshot, onSave, onClose, initialTab = 'route', suggestedCity = null, initialField = null }) {
  const [draft, setDraft] = useState(() => createTripDraft(snapshot?.trip));
  const [tab, setTab] = useState(initialTab);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const update = (key, value) => setDraft(current => ({ ...current, [key]: value }));
  const updateStay = (id, values) => setDraft(current => ({ ...current, stays: current.stays.map(stay => stay.id === id ? { ...stay, ...values } : stay) }));
  const updatePerson = (id, values) => setDraft(current => ({ ...current, travelers: current.travelers.map(person => person.id === id ? { ...person, ...values } : person) }));
  const updateGroup = (id, values) => setDraft(current => ({ ...current, groups: current.groups.map(group => group.id === id ? { ...group, ...values } : group) }));

  async function save(event) {
    event.preventDefault();
    setError(''); setSaving(true);
    try {
      const operations = buildTripOperations(snapshot?.trip, draft);
      if (await onSave(operations, false)) onClose();
    } catch (failure) { setError(failure.message); }
    finally { setSaving(false); }
  }

  return <Modal title="Соберём вашу поездку" subtitle="Добавляйте только то, что уже знаете. Параметры можно менять в любой момент." onClose={onClose} wide>
    <form onSubmit={save}>
      <div className="editor-tabs" role="tablist" aria-label="Параметры поездки">
        {[['route', 'Маршрут'], ['party', 'Гости и номера'], ['budget', 'Бюджет и условия']].map(([id, title]) => <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{title}</button>)}
      </div>
      <div className="editor-content">
        {tab === 'route' && <>
          {suggestedCity && !draft.stays.some(stay => stay.city === suggestedCity) && <div className="city-suggestion"><div><strong>Вы упомянули город: {suggestedCity}</strong><p>Выберите, как изменить маршрут. Сохраните изменения, когда всё будет готово.</p></div><div className="city-suggestion-actions">{draft.stays.length === 1 && <button type="button" className="secondary-button" onClick={() => updateStay(draft.stays[0].id, { city: suggestedCity, hotel_id: null })}>Заменить {draft.stays[0].city || 'город'}</button>}<button type="button" className="secondary-button" disabled={draft.stays.length >= 4} onClick={() => update('stays', [...draft.stays, { id: `stay-${uuid()}`, city: suggestedCity, check_in: null, check_out: null, rooms: [], hotel_id: null }])}>Добавить город</button></div></div>}
          <label>Город отправления<input list="cities" placeholder="Например, Казань" value={draft.origin} onChange={e => update('origin', e.target.value)} /></label>
          <div className="stays-editor">{draft.stays.map((stay, index) => <div className="stay-editor" key={stay.id}>
            <div className="section-title"><span className="step-number">{index + 1}</span><h3>Остановка {index + 1}</h3><button className="icon-button" type="button" onClick={() => update('stays', draft.stays.filter(item => item.id !== stay.id))} aria-label={`Удалить остановку ${index + 1}`}><Trash2 size={17} /></button></div>
            <label>Город<input list="cities" placeholder="Куда поедем?" value={stay.city || ''} onChange={e => updateStay(stay.id, { city: e.target.value, hotel_id: stay.city === e.target.value ? stay.hotel_id : null })} /></label>
            <div className="form-grid"><label>Заезд<input type="date" value={stay.check_in || ''} onChange={e => updateStay(stay.id, { check_in: e.target.value })} /></label><label>Выезд<input type="date" min={stay.check_in || undefined} value={stay.check_out || ''} onChange={e => updateStay(stay.id, { check_out: e.target.value })} /></label></div>
          </div>)}</div>
          <button type="button" className="text-button" disabled={draft.stays.length >= 4} onClick={() => update('stays', [...draft.stays, { id: `stay-${uuid()}`, city: null, check_in: null, check_out: null, rooms: [], hotel_id: null }])}><Plus size={17} /> Добавить город</button>
          <div className="form-grid route-policy"><label>Завершение маршрута<select value={draft.routeKind} onChange={e => update('routeKind', e.target.value)}><option value="round_trip">В город отправления</option><option value="one_way">В одну сторону</option><option value="open_jaw">Возвращение в другой город</option><option value="undecided">Пока не определились</option></select></label>{draft.routeKind === 'open_jaw' && <label>Конечный город<input list="cities" placeholder="Город возвращения" value={draft.endCity} onChange={e => update('endCity', e.target.value)} /></label>}</div>
        </>}
        {tab === 'party' && <>
          <label>Гражданство для поиска отеля<select data-initial-focus={initialField === 'nationality' ? '' : undefined} value={draft.nationality} onChange={e => update('nationality', e.target.value)}><option value="">Выберите страну</option>{draft.nationality && !COUNTRIES.some(country => country.code === draft.nationality) && <option value={draft.nationality}>{draft.nationality}</option>}{COUNTRIES.map(country => <option value={country.code} key={country.code}>{country.name}</option>)}</select></label><p className="hint">Укажите страну гражданства, которую использовать при подборе отеля.</p>
          <div className="section-title"><h3>Кто едет?</h3><span className="muted">До 10 гостей в текущей версии</span></div>
          {draft.travelers.length === 0 && <p className="hint">Добавьте участников, когда определитесь с составом поездки.</p>}
          {draft.travelers.map((person, index) => <div className="guest-editor" key={person.id}>
            <div className="person-row"><span className="person-number">{index + 1}</span><label className="sr-only" htmlFor={`type-${person.id}`}>Тип гостя {index + 1}</label><select id={`type-${person.id}`} value={person.type} onChange={e => updatePerson(person.id, { type: e.target.value, age: null, birth_date: null })}><option value="adult">Взрослый</option><option value="child">Ребёнок</option></select>{person.type === 'child' && <label className="age-field">Возраст<input type="number" min="0" max="17" placeholder="Уточним" value={person.age ?? ''} onChange={e => updatePerson(person.id, { age: e.target.value, birth_date: null })} /></label>}<button className="icon-button" type="button" onClick={() => update('travelers', draft.travelers.filter(item => item.id !== person.id))} aria-label={`Удалить гостя ${index + 1}`}><Trash2 size={16} /></button></div>
            {draft.groups.length > 0 && <label className="group-choice">Группа гостя {index + 1}<select value={person.group_id || ''} onChange={e => updatePerson(person.id, { group_id: e.target.value || null })}><option value="">Без группы</option>{draft.groups.map((group, i) => <option value={group.id} key={group.id}>Группа {i + 1}</option>)}</select></label>}
          </div>)}
          <button type="button" className="text-button" disabled={draft.travelers.length >= 10} onClick={() => update('travelers', [...draft.travelers, { id: `traveler-${uuid()}`, type: 'adult', age: null, birth_date: null, group_id: null }])}><Plus size={17} /> Добавить гостя</button>
          <div className="section-title mt-24"><h3>Семьи и группы</h3><button type="button" className="text-button" onClick={() => update('groups', [...draft.groups, { id: `group-${uuid()}`, traveler_ids: [], keep_separate: false }])}><Plus size={15} /> Добавить группу</button></div>
          {draft.groups.map((group, index) => <label className="checkbox-row" key={group.id}><input type="checkbox" checked={group.keep_separate} onChange={e => updateGroup(group.id, { keep_separate: e.target.checked })} /><span>Размещать группу {index + 1} отдельно от других групп</span></label>)}
          {draft.groups.length > 0 && <p className="hint">Выберите группу для каждого гостя выше. Состав сохраняется отдельно для каждой семьи.</p>}
          <h3 className="mt-24">Размещение по номерам</h3>
          {!draft.stays.length && <p className="hint">Сначала добавьте остановку на вкладке «Маршрут».</p>}
          {draft.stays.map((stay, stayIndex) => <div className="room-editor" key={stay.id}>
            <div className="section-title"><h4>{stay.city || `Остановка ${stayIndex + 1}`}</h4><button type="button" className="text-button" disabled={stay.rooms.length >= 5} onClick={() => updateStay(stay.id, { rooms: [...stay.rooms, { id: `room-${uuid()}`, traveler_ids: [] }] })}><Plus size={15} /> Номер</button></div>
            {stay.rooms.map((room, roomIndex) => <div className="room-label" key={room.id}><span>Номер {roomIndex + 1}</span><button type="button" className="icon-button" onClick={() => updateStay(stay.id, { rooms: stay.rooms.filter(item => item.id !== room.id) })} aria-label={`Удалить номер ${roomIndex + 1} в остановке ${stayIndex + 1}`}><Trash2 size={15} /></button></div>)}
            {draft.travelers.map((person, index) => <label className="allocation-row" key={person.id}><span>Гость {index + 1}{person.type === 'child' ? ' · ребёнок' : ''}</span><select value={stay.rooms.find(room => room.traveler_ids.includes(person.id))?.id || ''} onChange={e => updateStay(stay.id, { rooms: stay.rooms.map(room => ({ ...room, traveler_ids: [...room.traveler_ids.filter(id => id !== person.id), ...(room.id === e.target.value ? [person.id] : [])] })) })}><option value="">Выбрать номер</option>{stay.rooms.map((room, i) => <option value={room.id} key={room.id}>Номер {i + 1}</option>)}</select></label>)}
          </div>)}
          <p className="hint">Для поиска можно сначала сохранить неполные данные — агент уточнит недостающие параметры.</p>
        </>}
        {tab === 'budget' && <><h3>На какую сумму рассчитываете?</h3><div className="form-grid"><label>Бюджет на всю поездку<input inputMode="decimal" placeholder="Можно указать позже" value={draft.budget} onChange={e => update('budget', e.target.value)} /></label><label>Валюта<select value={draft.currency} onChange={e => update('currency', e.target.value)}><option value="RUB">Рубли · RUB</option><option value="USD">Доллары · USD</option><option value="EUR">Евро · EUR</option></select></label></div><h3 className="mt-24">Обязательные условия</h3>{[['breakfast', 'Завтрак включён'], ['refundable', 'Возвратный тариф'], ['nonstop', 'Только прямые перелёты']].map(([key, label]) => <label className="checkbox-row" key={key}><input type="checkbox" checked={draft[key]} onChange={e => update(key, e.target.checked)} /><span>{label}</span></label>)}<p className="hint">Дополнительные условия отдельных номеров сохраняются. Неподтверждённые свойства не выдаются за гарантию.</p></>}
        <datalist id="cities">{CITIES.map(city => <option key={city} value={city} />)}</datalist>
        {error && <p className="form-error" role="alert">{error}</p>}
      </div>
      <footer className="modal-footer"><span className="muted">Предложения обновятся после сохранения.</span><button className="primary-button" disabled={saving} type="submit">{saving && <LoaderCircle className="spin" size={18} />}Сохранить <ArrowRight size={17} /></button></footer>
    </form>
  </Modal>;
}
