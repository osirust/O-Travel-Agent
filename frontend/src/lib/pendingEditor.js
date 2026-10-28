export function pendingEditorTarget(pending) {
  if (pending?.field === 'search.guest_nationality' || /гражданств/i.test(pending?.text || '')) return { tab: 'party', field: 'nationality' };
  if (/^budget\./.test(pending?.field || '') || /бюджет/i.test(pending?.text || '')) return { tab: 'budget', field: null };
  if (/^(party\.|traveler\.|group\.|stay\.rooms)/.test(pending?.field || '')) return { tab: 'party', field: null };
  return { tab: 'route', field: null };
}

export function chatAcceptsText(modelUnavailable, pendingAnswer) {
  return !modelUnavailable || pendingAnswer;
}
