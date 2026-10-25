"use strict";
const $ = id => document.getElementById(id);
const savedKey = `otravel.debug.session.${location.origin}`;
let access = null, snapshot = null, busy = false, tracked = new Set(), messages = new Set();
let epoch = 0;
const uuid = () => crypto.randomUUID();
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function node(tag, text, cls) { const n = document.createElement(tag); if (text != null) n.textContent = String(text); if (cls) n.className = cls; return n; }
function showNotice(text) { $("notice").textContent = text || ""; $("notice").hidden = !text; }
function money(amount, currency = "RUB") { try { return new Intl.NumberFormat("ru-RU", {style:"currency",currency,maximumFractionDigits:0}).format(amount / 100); } catch { return `${amount / 100} ${currency}`; } }
function date(value) { if (!value) return "дата не выбрана"; const d = new Date(`${value}T12:00:00`); return Number.isNaN(d.valueOf()) ? value : new Intl.DateTimeFormat("ru-RU", {day:"numeric",month:"short"}).format(d); }
function addMessage(role, text, id, meta) {
  if (id && messages.has(id)) return;
  if (id) messages.add(id);
  const article = node("article", null, `message ${role}`);
  article.append(node("span", role === "user" ? "Вы" : "O!Travel Agent", "who"), node("div", text, "body"));
  if (meta) article.append(node("div", meta, "meta"));
  $("messages").append(article); $("messages").scrollTop = $("messages").scrollHeight;
}
function setBusy(value) {
  busy = value; $("send").disabled = value; $("demo").disabled = value; $("undo").disabled = value;
  $("activity").textContent = value ? "Агент обрабатывает запрос…" : "Enter — отправить · Shift + Enter — новая строка";
}
async function api(path, method = "GET", body, token = true) {
  const headers = {"Content-Type":"application/json"};
  if (token && access) headers.Authorization = `Bearer ${access.token}`;
  const response = await fetch(path, {method, headers, body: body == null ? undefined : JSON.stringify(body), cache:"no-store"});
  let data; try { data = await response.json(); } catch { throw new Error(`Сервер ответил ${response.status}. Повторите после восстановления сервиса.`); }
  if (!response.ok) { const e = new Error(data.error?.message || `Ошибка ${response.status}`); e.code = data.error?.code; e.status = response.status; throw e; }
  return data;
}
function sessionPath(suffix = "") { if (!access) throw new Error("Сначала создайте поездку"); return `/api/v1/sessions/${access.id}${suffix}`; }
function field(label, value) { const block = node("div", null, "trip-item"); block.append(node("span", label, "trip-label"), node("div", value, "trip-value")); return block; }
function renderTrip(data) {
  snapshot = data; const t = data.trip; const box = $("trip-summary"); box.replaceChildren();
  const paused = data.waiting_reason === "user_pause";
  $("pause").disabled = paused; $("resume").disabled = !paused;
  $("version").textContent = `${paused ? "Пауза · " : ""}версия ${data.state_version}`;
  if (data.pending_question?.requested_destination) {
    const request = data.pending_question.requested_destination;
    box.append(field("Направление", `${request.name}${request.kind === "country" ? " · выбираем город" : ""}`));
  }
  box.append(field("Отправление", t.origin || "Пока не выбрано"));
  const adults = t.travelers.filter(p => p.type === "adult").length, children = t.travelers.filter(p => p.type === "child").length;
  box.append(field("Путешественники", adults || children ? `${adults} взр. · ${children} дет.` : "Уточним в разговоре"));
  box.append(field("Общий бюджет", t.budget ? money(t.budget.amount_minor,t.budget.currency) : "Пока не задан"));
  if (t.stays.length) {
    const route = node("div", null, "trip-route");
    t.stays.forEach(stay => { const s = node("div", null, "stay"); s.append(node("strong", stay.city || "Город"), node("small", `${date(stay.check_in)} — ${date(stay.check_out)} · ${stay.rooms.length} ном.`)); route.append(s); });
    box.append(node("span", "Маршрут", "trip-label"),route);
  } else box.append(field("Маршрут", "Города и даты ещё не выбраны"));
  $("state-json").textContent = JSON.stringify(t,null,2);
  if (paused) showNotice("Пауза сохранена. Дополнения можно отправлять; агент продолжит только после кнопки «Продолжить» или команды «продолжай».");
}
function renderPlans(plans) {
  if (!plans?.length) return;
  const box = $("plans"); box.replaceChildren();
  plans.forEach((p,i) => { const card = node("div", null, "plan"), top = node("div", null, "plan-top");
    top.append(node("strong", p.total_minor == null ? "Нужна проверка суммы" : money(p.total_minor,p.currency)),node("span",p.source_type || "Не проверено","source"));
    card.append(top,node("p",`Вариант ${i + 1} · ${p.offers?.length || 0} компонентов · ${p.source_type === "DEMO" ? "вымышленные цены" : "оценочная стоимость"}`)); box.append(card); });
}
async function refresh() {
  if (!access) return;
  const data = await api(sessionPath()); renderTrip(data);
  const history = await api(sessionPath("/messages"));
  history.messages.forEach(m => { if (m.role === "assistant" && m.text || m.role === "user" && !m.command) addMessage(m.role,m.text,m.id); });
  return data;
}
function modelInfo(meta) {
  if (!meta) return;
  $("model").textContent = meta.model || meta.provider;
  $("model-detail").textContent = `${meta.provider || "LLM"}${meta.latency_ms != null ? ` · ${(meta.latency_ms / 1000).toFixed(1)} с` : ""} · попыток: ${meta.attempts?.length || 1}`;
}
async function poll(runId, runEpoch) {
  tracked.add(runId); const deadline = Date.now() + 115000;
  try {
    while (Date.now() < deadline && runEpoch === epoch) {
      const run = await api(`/api/v1/runs/${runId}`); modelInfo(run.llm);
      if (!["pending","running"].includes(run.status)) {
        tracked.delete(runId); await refresh();
        const result = run.result?.result || {};
        renderPlans(result.plans);
        if (result.no_changes && result.model_reply && !result.operation_normalizations?.length && result.model_reply !== run.result?.reply)
          addMessage("assistant",result.model_reply,`model-${runId}`,"Текст модели · состояние поездки не менялось");
        if (run.error) { addMessage("error",run.error.message,`error-${runId}`); showNotice("Состояние сохранено. Можно уточнить запрос, воспользоваться демопримером или повторить позже."); }
        else if (run.status === "cancelled") showNotice("Обработка отменена. Поездка сохранена.");
        else if (snapshot?.waiting_reason !== "user_pause") showNotice("");
        return;
      }
      const data = await refresh();
      if (data?.waiting_reason === "user_pause" && run.status === "pending") return;
      await sleep(600);
    }
    if (runEpoch === epoch) showNotice("Ожидание затянулось. Запрос сохранён; обновите страницу, чтобы проверить результат. Не отправляйте его повторно с новым текстом.");
  } finally { if (runEpoch === epoch) setBusy(false); }
}
async function createSession() {
  epoch++; tracked = new Set(); messages = new Set(); $("messages").replaceChildren(); $("plans").replaceChildren(node("p","После поиска здесь появятся варианты.","muted"));
  const data = await api("/api/v1/sessions","POST",{},false);
  access = {id:data.session_id,token:data.session_token}; localStorage.setItem(savedKey,JSON.stringify(access)); renderTrip(data); showNotice("");
  addMessage("assistant","Расскажи, куда и когда хочешь поехать, кто едет и какой бюджет. Или загрузи сложный пример и попробуй изменить его условия.","welcome"); setBusy(false); $("prompt").focus();
}
async function action(kind) {
  if (!access) await createSession();
  showNotice("");
  if (["pause","resume"].includes(kind)) {
    await api(sessionPath("/control"),"POST",{action:kind}); await refresh();
    if (kind === "resume") { setBusy(true); const ownEpoch = epoch; for (const id of [...tracked]) await poll(id,ownEpoch); setBusy(false); }
    return;
  }
  setBusy(true); const body = {client_message_id:uuid(),expected_version:snapshot.state_version};
  const accepted = await api(sessionPath(kind === "undo" ? "/control" : "/demo"),"POST",kind === "undo" ? {...body,action:"undo"} : body);
  await poll(accepted.run_id,epoch);
}
$("composer").addEventListener("submit",async e => {
  e.preventDefault(); if (busy) return;
  const text = $("prompt").value.trim(); if (!text) return;
  try { if (!access) await createSession(); setBusy(true); showNotice("");
    const id = uuid(), accepted = await api(sessionPath("/messages"),"POST",{client_message_id:id,text,expected_version:snapshot.state_version});
    $("prompt").value = ""; await refresh(); await poll(accepted.run_id,epoch);
  } catch(error) { showNotice(error.message); setBusy(false); }
});
$("prompt").addEventListener("keydown",e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); } });
for (const name of ["demo","pause","resume","undo"]) $(name).addEventListener("click",() => action(name).catch(e => {showNotice(e.message);setBusy(false);}));
$("new-session").addEventListener("click",() => createSession().catch(e => showNotice(e.message)));
async function init() {
  setBusy(true);
  try {
    const health = await api("/health","GET",null,false); $("connection").textContent = health.database === "ready" ? "Backend и БД доступны" : "Проверяем подключение";
    const providers = await api("/api/v1/providers/status","GET",null,false);
    $("runtime").textContent = providers._runtime?.llm_provider === "ollama" ? "Локальная модель" : "API-режим";
    if (providers._runtime?.model) $("model").textContent = providers._runtime.model;
    try { access = JSON.parse(localStorage.getItem(savedKey)); } catch { access = null; }
    if (access?.id && access?.token) {
      try { await refresh(); const history = await api(sessionPath("/messages"));
        const ids = [...new Set(history.messages.filter(m => m.role === "user" && m.run_id).map(m => m.run_id))].slice(-5);
        for (const id of ids) { const run = await api(`/api/v1/runs/${id}`); if (["pending","running"].includes(run.status)) { tracked.add(id); if (snapshot.waiting_reason !== "user_pause") {setBusy(true);poll(id,epoch).catch(e => showNotice(e.message));} } else renderPlans(run.result?.result?.plans); }
      } catch(error) { if ([401,403,404].includes(error.status)) await createSession(); else throw error; }
      if (!tracked.size || snapshot.waiting_reason === "user_pause") setBusy(false);
    } else await createSession();
  } catch(error) { $("connection").textContent = "Нет соединения"; showNotice(error.message); setBusy(false); }
}
init();
