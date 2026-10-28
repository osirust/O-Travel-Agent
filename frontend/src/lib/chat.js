const GENERIC_FAILURE = /^Не удалось завершить обработку\. Поездка сохранена; доступны формы и явный DEMO-сценарий\.$/i;

export function projectMessageRunError(message, runs, sessionId) {
  if (message.role !== 'assistant' || !message.run_id || !GENERIC_FAILURE.test(message.text || '')) return message;
  const run = runs.get(message.run_id);
  if (run?.run_id !== message.run_id || run?.session_id !== sessionId || run?.status !== 'failed'
      || run.error?.code !== 'NO_MATCHES' || !/^Google Flights не вернул предложений(?=[\s.,;:!?]|$)/u.test(run.error?.message || '')) return message;
  // A view-only, fixed-code projection. Do not persist raw provider text,
  // rewrite canonical history or borrow an error from another background run.
  return { ...message, viewRunError: { run_id: run.run_id, code: 'NO_MATCHES', source: 'google_flights' } };
}

export function messageText(message) {
  if (message.kind === 'command') return 'Параметры поездки обновлены';
  if (message.role !== 'assistant') return message.text;
  const text = message.text || '';
  if (GENERIC_FAILURE.test(text)) {
    if (message.viewRunError?.run_id === message.run_id && message.viewRunError?.code === 'NO_MATCHES'
        && message.viewRunError?.source === 'google_flights') return 'Google Flights не вернул предложений по этому маршруту и датам. Это не доказывает, что рейсов нет. Проверьте маршрут и даты или попробуйте поиск позже.';
    return 'Не удалось обработать запрос. Параметры поездки сохранены — их можно изменить в форме.';
  }
  if (/^Подобрано DEMO-планов:\s*\d+\. Все цены и наличие вымышлены; бронь не создана\.$/i.test(text)) return 'Это пример поездки. Цены и наличие мест пока не подтверждены.';
  if (/^Получены компоненты поставщика\.?$/i.test(text)) return 'Варианты готовы. Посмотрите перелёты и отели в соседнем разделе.';
  return text;
}
