// A complete trip can be sent to the existing plan builder without guessing
// missing dates, travelers or room allocation.
export function agentProposalReady(trip) {
  if (!trip?.origin || !trip.stays?.length || !trip.travelers?.some(person => person.type === 'adult')) return false;
  if (trip.travelers.some(person => person.type === 'child' && person.age == null && !person.birth_date)) return false;
  if (!trip.route_policy?.kind || trip.route_policy.kind === 'undecided' || trip.route_policy.kind === 'open_jaw' && !trip.route_policy.end_city) return false;
  return trip.stays.every(stay => stay.city && stay.check_in && stay.check_out && stay.check_out > stay.check_in && stay.rooms?.length && stay.rooms.every(room => room.traveler_ids?.length));
}
