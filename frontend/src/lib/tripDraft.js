import { minorUnits } from './travel.js';

export function createTripDraft(trip = {}) {
  return {
    origin: trip.origin || '',
    travelers: structuredClone(trip.travelers || []),
    groups: structuredClone(trip.groups || []),
    stays: structuredClone(trip.stays || []),
    budget: trip.budget ? String(trip.budget.amount_minor / 100) : '',
    currency: trip.budget?.currency || 'RUB',
    mode: trip.search_policy?.mode || 'DEMO',
    hotelProvider: trip.search_policy?.hotel_provider || 'liteapi',
    flightProvider: trip.search_policy?.flight_provider || 'serpapi',
    nationality: trip.search_policy?.guest_nationality || '',
    fallback: trip.search_policy?.demo_fallback ?? true,
    routeKind: trip.route_policy?.kind || 'round_trip',
    endCity: trip.route_policy?.end_city || '',
    breakfast: trip.constraints?.find(item => item.key === 'breakfast' && !item.stay_id)?.value ?? false,
    refundable: trip.constraints?.find(item => item.key === 'refundable' && !item.stay_id)?.value ?? false,
    nonstop: trip.constraints?.find(item => item.key === 'nonstop')?.value ?? false,
  };
}

export function buildTripOperations(trip = {}, draft) {
  const operations = [];
  const oldPeople = trip.travelers || [], oldStays = trip.stays || [], oldGroups = trip.groups || [];
  const changed = (a, b) => JSON.stringify(a) !== JSON.stringify(b);
  const origin = draft.origin.trim();
  if (!origin && trip.origin) throw new Error('Укажите новый город отправления.');
  if (origin && trip.origin !== origin) operations.push({ type: 'set_origin', value: { city: origin } });
  if (!draft.budget.trim() && trip.budget) throw new Error('Укажите бюджет: удаление бюджета через форму пока не поддерживается.');
  if (draft.budget.trim()) {
    const budget = { amount_minor: minorUnits(draft.budget), currency: draft.currency, scope: 'whole_trip' };
    if (changed(trip.budget, budget)) operations.push({ type: 'set_budget', value: budget });
  }
  oldPeople.filter(person => !draft.travelers.some(item => item.id === person.id)).forEach(person => operations.push({ type: 'remove_traveler', target_id: person.id, value: {} }));
  for (const person of draft.travelers) {
    const old = oldPeople.find(item => item.id === person.id);
    const value = {
      type: person.type,
      age: person.age === '' || person.age == null ? null : Number(person.age),
      birth_date: person.birth_date || null,
      // Membership is changed only by upsert_group below, after all people exist.
      group_id: old?.group_id || null,
    };
    if (value.age !== null && (!Number.isInteger(value.age) || value.age < 0 || value.age > (value.type === 'child' ? 17 : 120))) throw new Error('Проверьте возраст путешественника.');
    if (person.group_id && !draft.groups.some(group => group.id === person.group_id)) throw new Error('Выберите существующую группу для каждого путешественника.');
    if (!old) operations.push({ type: 'add_traveler', value: { id: person.id, ...value } });
    else if (changed({ type: old.type, age: old.age, birth_date: old.birth_date, group_id: old.group_id }, value)) operations.push({ type: 'update_traveler', target_id: person.id, value });
  }
  for (const group of draft.groups) {
    const travelerIds = draft.travelers.filter(person => person.group_id === group.id).map(person => person.id);
    const previous = oldGroups.find(item => item.id === group.id);
    const value = { traveler_ids: travelerIds, keep_separate: Boolean(group.keep_separate) };
    // Removal already updates group membership; other group edits remain explicit.
    const prior = previous && { traveler_ids: previous.traveler_ids.filter(id => draft.travelers.some(person => person.id === id)), keep_separate: previous.keep_separate };
    if (!previous || changed(prior, value)) operations.push({ type: 'upsert_group', target_id: group.id, value });
  }
  oldStays.filter(stay => !draft.stays.some(item => item.id === stay.id)).forEach(stay => operations.push({ type: 'remove_stay', target_id: stay.id, value: {} }));
  for (const stay of draft.stays) {
    if (stay.check_in && stay.check_out && stay.check_out <= stay.check_in) throw new Error('Выезд должен быть позже заезда.');
    const rooms = stay.rooms.map(room => ({ id: room.id, traveler_ids: room.traveler_ids.filter(id => draft.travelers.some(person => person.id === id)) }));
    const assigned = rooms.flatMap(room => room.traveler_ids);
    if (assigned.length !== new Set(assigned).size) throw new Error('Один гость не может одновременно жить в двух номерах одной остановки.');
    const old = oldStays.find(item => item.id === stay.id);
    const value = { city: stay.city?.trim() || null, check_in: stay.check_in || null, check_out: stay.check_out || null, rooms, hotel_id: stay.hotel_id || null,
      ...(old?.place_id && old.city === stay.city?.trim() ? { place_id: old.place_id } : {}) };
    if (!old || changed({ city: old.city, check_in: old.check_in, check_out: old.check_out, rooms: old.rooms, hotel_id: old.hotel_id, ...(old.place_id ? { place_id: old.place_id } : {}) }, value)) operations.push({ type: 'upsert_stay', target_id: stay.id, value });
  }
  const route = { kind: draft.routeKind, ...(draft.routeKind === 'open_jaw' ? { end_city: draft.endCity.trim() || null } : {}) };
  if (route.kind === 'open_jaw' && !route.end_city) throw new Error('Укажите конечный город поездки.');
  if (trip.route_policy?.kind !== route.kind || (route.kind === 'open_jaw' && trip.route_policy?.end_city !== route.end_city)) operations.push({ type: 'set_route_policy', value: route });
  for (const key of ['breakfast', 'refundable', 'nonstop']) {
    const previous = trip.constraints?.find(item => item.key === key && !item.stay_id);
    if (draft[key] && previous?.value !== true) operations.push({ type: 'set_constraint', value: { key, value: true, hard: true } });
    else if (!draft[key] && previous?.value === true) operations.push({ type: 'clear_constraint', value: { key } });
  }
  const policy = { mode: draft.mode, hotel_provider: draft.hotelProvider, flight_provider: draft.flightProvider, demo_fallback: draft.fallback, guest_nationality: draft.nationality.trim().toUpperCase() || null };
  if (policy.guest_nationality && !/^[A-Z]{2}$/.test(policy.guest_nationality)) throw new Error('Гражданство указывается двухбуквенным кодом, например RU или TR.');
  if (changed(trip.search_policy, policy)) operations.push({ type: 'set_search_policy', value: policy });
  if (operations.length > 30) throw new Error('Слишком много изменений за один раз. Сохраните поездку в несколько шагов.');
  return operations;
}
