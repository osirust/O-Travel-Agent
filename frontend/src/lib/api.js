export class ApiError extends Error {
  constructor(message, status = 0, code = 'network_error') { super(message); this.status = status; this.code = code; }
}

export class TravelApi {
  constructor({ fetcher = globalThis.fetch.bind(globalThis), baseUrl = '' } = {}) { this.fetcher = fetcher; this.baseUrl = baseUrl; }
  async request(path, { method = 'GET', body, token, signal, timeout = 75000 } = {}) {
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (signal?.aborted) controller.abort();
    else signal?.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(abort, timeout);
    try {
      const response = await this.fetcher(`${this.baseUrl}${path}`, {
        method, cache: 'no-store', signal: controller.signal,
        headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      let data;
      try { data = await response.json(); }
      catch { throw new ApiError('Сервер вернул неожиданный ответ. Проверьте подключение к backend.', response.status, 'invalid_response'); }
      if (!response.ok) throw new ApiError(data.error?.message || `Запрос не выполнен (${response.status}).`, response.status, data.error?.code);
      return data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (signal?.aborted) throw error;
      throw new ApiError(error.name === 'AbortError' ? 'Ожидание затянулось. Проверьте результат сохранённого запроса.' : 'Не удалось связаться с сервером. Введённые данные сохранены.');
    } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
  }
}
