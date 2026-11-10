'use strict';
const csrf = document.querySelector('meta[name="csrf-token"]').content;
async function send(path, body = {}, keepalive = false) {
  const response = await fetch(path, {method: 'POST', credentials: 'same-origin',
    signal: AbortSignal.timeout(path.endsWith('/ai') ? 75000 : 15000),
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: JSON.stringify(body), keepalive});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Could not save. Please retry.');
  return result;
}
function bindForm(id, action) {
  const form = document.getElementById(id);
  if (!form) return;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('[type=submit]');
    const status = form.querySelector('.status');
    if (button.disabled) return;
    button.disabled = true;
    status.textContent = 'Saving…';
    try { await action(new FormData(form), status); }
    catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
}
bindForm('identify-form', async values => {
  const result = await send('/identify', Object.fromEntries(values));
  window.location.assign(result.redirect);
});
const study = document.getElementById('study');
if (study) {
  const root = `/sessions/${study.dataset.sessionId}`;
  bindForm('begin-form', async () => {await send(`${root}/begin`); window.location.reload();});
  bindForm('attention-form', async (values, status) => {
    const result = await send(`${root}/attention`, {answer: Number(values.get('answer'))});
    if (result.passed) window.location.reload();
    else if (result.restart) {
      status.textContent = 'Please review the instructions, then select option C on the check.';
      const link = document.createElement('a'); link.href = window.location.href;
      link.textContent = ' Review instructions'; status.append(link);
      document.querySelector('#attention-form button').hidden = true;
    } else status.textContent = 'The instruction asks you to select option C. Please try again.';
  });
  bindForm('comment-form', async values => {await send(`${root}/complete`, {comment: values.get('comment')}); window.location.reload();});
}
const trial = document.getElementById('trial');
if (trial) setupTrial(trial);

function setupTrial(element) {
  const root = `/trials/${element.dataset.trialId}`;
  const pageId = crypto.randomUUID();
  let visibleMs = 0;
  let lastTick = performance.now();
  let wasVisible = document.visibilityState === 'visible';
  let submitting = false;
  let finished = false;
  let eventQueue = Promise.resolve();
  const ackQueue = new Map();
  const rendered = new Set();
  const waitingToRender = new Map();
  const assistant = document.getElementById('messages');
  const aiStatus = document.getElementById('ai-status');
  const retryButton = document.getElementById('ai-retry');
  let pendingRequest = null;
  let retryCall = null;
  let aiBusy = false;
  let pollTimer;

  function tick() {
    const current = performance.now();
    if (wasVisible) visibleMs += current - lastTick;
    lastTick = current; wasVisible = document.visibilityState === 'visible';
  }
  function checkpoint(kind, value, keepalive = false) {
    tick();
    const body = {id: crypto.randomUUID(), page_id: pageId, kind, visible_ms: visibleMs};
    if (value !== undefined) body.value = value;
    eventQueue = eventQueue.catch(() => {}).then(() => send(`${root}/event`, body, keepalive));
    return eventQueue;
  }
  const visibleReady = document.visibilityState === 'visible' ? checkpoint('visible') : Promise.resolve();
  visibleReady.catch(() => {});
  const heartbeat = setInterval(() => {
    if (!finished && !submitting && document.visibilityState === 'visible') checkpoint('checkpoint').catch(() => {});
  }, 10000);
  document.addEventListener('visibilitychange', () => {
    if (finished || submitting) return;
    checkpoint(document.visibilityState === 'visible' ? 'visible' : 'hidden', undefined, true).catch(() => {});
    if (document.visibilityState === 'visible') {
      for (const call of waitingToRender.values()) displayCall(call);
      waitingToRender.clear();
      if (assistant) startSuggestion();
    }
  });
  window.addEventListener('pagehide', () => {
    if (!finished && !submitting) checkpoint('leave', undefined, true).catch(() => {});
    clearInterval(heartbeat); clearTimeout(pollTimer);
  });
  document.getElementById('answer-form').addEventListener('change', event => {
    if (['answer','confidence'].includes(event.target.name)) {
      checkpoint(event.target.name === 'answer' ? 'answer_change' : 'confidence_change', Number(event.target.value)).catch(() => {});
    }
  });
  bindForm('answer-form', async values => {
    submitting = true;
    try {
      await checkpoint('checkpoint').catch(() => {});
      // Flush display receipts before saving. Failed receipts remain conservatively unobserved.
      await Promise.race([Promise.allSettled([...ackQueue.values()]), new Promise(resolve => setTimeout(resolve, 1200))]);
      await send(`${root}/submit`, {answer: Number(values.get('answer')), confidence: Number(values.get('confidence'))});
      finished = true; clearInterval(heartbeat); clearTimeout(pollTimer);
      window.location.reload();
    } catch (error) {
      submitting = false;
      for (const call of waitingToRender.values()) displayCall(call);
      waitingToRender.clear();
      throw error;
    }
  });
  if (!assistant) return;

  function displayCall(call) {
    if (finished || rendered.has(call.id) || call.status === 'pending') return;
    if (submitting || document.visibilityState !== 'visible') {waitingToRender.set(call.id, call); return;}
    if (!call.text) return;
    if (call.user_text) {
      const user = document.createElement('p'); user.className = 'message user';
      user.textContent = `You: ${call.user_text}`; assistant.append(user);
    }
    const message = document.createElement('p'); message.className = 'message';
    message.textContent = call.text; assistant.append(message);
    rendered.add(call.id);
    // RequestAnimationFrame runs after DOM insertion; a second frame allows a paint before acknowledgment.
    const receipt = new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      .then(() => {
        if (!finished && document.visibilityState === 'visible') return send(`${root}/ai/${call.id}/ack`);
      }).catch(() => {});
    ackQueue.set(call.id, receipt);
  }
  async function poll() {
    if (finished || submitting) return;
    try {
      const response = await fetch(`${root}/ai`);
      if (!response.ok) throw new Error('Could not restore assistance. Please retry.');
      const result = await response.json();
      const latest = new Map();
      for (const call of result.calls) latest.set(call.slot, call);
      let pending = false;
      retryCall = null;
      for (const call of latest.values()) {
        if (call.status === 'pending') pending = true;
        else displayCall(call);
        if (call.retry_allowed) retryCall = call;
      }
      retryButton.hidden = !retryCall;
      aiStatus.textContent = pending ? 'Preparing a response… You can answer at any time.' : 'Choose your answer independently of the suggestion.';
      if (pending) pollTimer = setTimeout(poll, 1500);
      return result.calls;
    } catch (error) {aiStatus.textContent = error.message; retryButton.hidden = false; return [];}
  }
  async function callAI(body) {
    if (aiBusy || submitting || finished) return;
    aiBusy = true; pendingRequest = body; retryButton.hidden = true;
    aiStatus.textContent = 'Preparing a response… You can answer at any time.';
    try {
      const result = await send(`${root}/ai`, body);
      pendingRequest = null;
      if (!finished) {
        displayCall(result);
        if (result.retry_allowed) retryCall = result;
        retryButton.hidden = !result.retry_allowed;
        aiStatus.textContent = result.status === 'pending' ? 'Preparing a response…' : 'You can answer whenever you are ready.';
        if (result.status === 'pending') pollTimer = setTimeout(poll, 1500);
      }
    } catch (error) {
      aiStatus.textContent = error.message; retryButton.hidden = false;
    } finally {aiBusy = false;}
  }
  let suggestionStarted = false;
  async function startSuggestion() {
    if (suggestionStarted || document.visibilityState !== 'visible') return;
    suggestionStarted = true;
    try {
      await visibleReady; await checkpoint('visible');
      const calls = await poll();
      if (!calls.length) await callAI({kind: 'suggestion', request_id: crypto.randomUUID()});
    } catch (error) {aiStatus.textContent = 'Could not load assistance. You can retry or answer now.'; retryButton.hidden = false;}
  }
  retryButton.addEventListener('click', () => {
    const body = pendingRequest || (retryCall ? {kind: retryCall.kind, text: retryCall.user_text || '', retry: true, request_id: crypto.randomUUID()}
      : {kind: 'suggestion', request_id: crypto.randomUUID()});
    callAI(body);
  });
  document.querySelectorAll('.prompt').forEach(button => button.addEventListener('click', () => {
    const input = document.getElementById('chat'); input.value = button.textContent; input.focus();
  }));
  document.getElementById('chat-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (aiBusy) return;
    const input = document.getElementById('chat');
    await callAI({kind: 'chat', text: input.value.trim(), request_id: crypto.randomUUID()});
    if (!pendingRequest) input.value = '';
  });
  startSuggestion();
}
