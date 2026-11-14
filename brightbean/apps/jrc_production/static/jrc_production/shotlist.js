// JR Content P1-B09: Shotlist tab. Forms work without JavaScript (redirect back to the tab);
// with it, each change re-renders only the list, so ticking items on a phone keeps the place.
(() => {
  const root = document.querySelector('[data-shotlist]');
  const status = document.querySelector('[data-shotlist-status]');
  if (!root || !window.fetch || !window.FormData) return;

  function restoreFocus(action, item) {
    let target = null;
    if (action === 'add' || action === 'delete') target = root.querySelector('[data-shotlist-new]');
    else if (item) {
      const row = document.getElementById(`shotlist-item-${item}`);
      if (row) {
        const selector = action === 'up' ? '[data-shotlist-focus-up]' : action === 'down' ? '[data-shotlist-focus-down]' : '[data-shotlist-focus]';
        target = row.querySelector(`${selector}:not([disabled])`) || row.querySelector('[data-shotlist-focus]');
      }
    }
    if (target) target.focus();
  }

  const UNSURE =
    'The connection dropped, so the change may or may not have been saved. ' +
    'The list below is the current one: check it before trying again.';
  const OFFLINE = 'The connection dropped and the list could not be loaded. Reload the page before trying again.';

  function showAlert(message) {
    const alert = document.createElement('p');
    alert.className = 'shotlist-error';
    alert.setAttribute('role', 'alert');
    alert.textContent = message;
    // Next to the add form, where the focus goes, so it is on screen on a phone too.
    const form = root.querySelector('[data-shotlist-new]')?.closest('form');
    if (form) form.before(alert);
    else root.prepend(alert);
  }

  // P2-B08: each item's text and note are edited right in place and save automatically (autosave.js).
  let inlineSavers = [];
  async function saveInline(form) {
    if (!form.elements.text.value.trim()) throw new Error('An item needs a text. Type one to save.');
    let response;
    try {
      response = await fetch(form.getAttribute('action'), {
        method: 'POST',
        body: new FormData(form),
        credentials: 'same-origin',
        headers: { 'X-JRC-Shotlist-Autosave': '1', Accept: 'application/json' },
      });
    } catch (error) {
      const failure = new Error('No connection.');
      failure.retry = true;
      throw failure;
    }
    const result = await response.json().catch(() => null);
    if (response.redirected || !response.ok || !result) {
      const failure = new Error((result && result.error) || 'The change could not be saved. Reload the page.');
      failure.retry = response.status >= 500 || (!result && !response.redirected);
      throw failure;
    }
    const row = form.closest('.shotlist-row');
    const meta = row && row.querySelector('[data-shotlist-meta]');
    if (result.meta && row) {
      if (meta) meta.textContent = result.meta;
      else {
        const line = document.createElement('p');
        line.className = 'shotlist-meta';
        line.dataset.shotlistMeta = '';
        line.textContent = result.meta;
        form.after(line);
      }
    }
    if (row) {
      row.querySelectorAll('[data-shotlist-focus]').forEach((box) => box.setAttribute('aria-label', result.text));
    }
    return { message: 'Saved' };
  }
  function initInline() {
    inlineSavers.forEach((saver) => saver.stop());
    inlineSavers = [];
    if (!window.jrcAutosave) return;
    root.querySelectorAll('[data-shotlist-inline]').forEach((form) => {
      const saver = window.jrcAutosave.create({
        root: form,
        status: form.querySelector('[data-save-status]'),
        send: () => saveInline(form),
      });
      form.jrcSaver = saver;
      form.addEventListener('input', () => saver.changed());
      form.addEventListener('focusout', () => { if (saver.dirty()) saver.flush(); });
      inlineSavers.push(saver);
    });
  }

  function showList(html) {
    root.innerHTML = html;
    initInline();
    const progress = root.querySelector('[data-shotlist-progress]');
    if (status && progress) status.textContent = `Shotlist ${progress.textContent.trim()}`;
  }

  // After an unknown outcome (no answer, or a server error) never send the change again: an
  // add or a move that was saved would be applied twice. Load the current list instead (GET).
  async function reloadAfterUnsure(action, data) {
    try {
      // P2-B14: the list of the format that is open.
      const format = root.querySelector('[data-shotlist-current-format]')?.dataset.shotlistCurrentFormat || '';
      const query = format ? `&format=${encodeURIComponent(format)}` : '';
      const response = await fetch(`${window.location.pathname}?tab=shotlist${query}`, { credentials: 'same-origin' });
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const fresh = response.ok && !response.redirected && page.querySelector('[data-shotlist]');
      if (!fresh) throw new Error('No shotlist in the page');
      showList(fresh.innerHTML);
      showAlert(UNSURE);
      if (action === 'add') {
        // Keep what was typed so nothing is lost, but leave sending it again to the person.
        const text = root.querySelector('[data-shotlist-new]');
        const note = root.querySelector('#shotlist-new-note');
        if (text) text.value = data.get('text') || '';
        if (note) note.value = data.get('note') || '';
      }
    } catch (error) {
      showAlert(OFFLINE);
    }
    const field = root.querySelector('[data-shotlist-new]');
    if (field) field.focus();
  }

  // P2-B14: the format sub-tabs open another format's list without a page load (content-tabs.js);
  // the address changes in place so a reload or "Copy link" keeps the format.
  root.addEventListener('click', async (event) => {
    const link = event.target.closest('[data-shotlist-format]');
    if (!link || !window.jrcTabs || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (link.getAttribute('aria-current') === 'page') return;
    const url = new URL(link.getAttribute('href'), window.location.href).href;
    if (await window.jrcTabs.show(url)) {
      history.replaceState({ jrcTab: 'shotlist' }, '', url);
      document.querySelector(`[data-shotlist-format="${link.dataset.shotlistFormat}"]`)?.focus();
    }
  });

  root.addEventListener('submit', async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !root.contains(form)) return;
    event.preventDefault();
    if (form.hasAttribute('data-shotlist-inline')) {
      // Enter in an item's text: save it now instead of waiting.
      if (form.jrcSaver) form.jrcSaver.flush();
      return;
    }
    // Typed changes reach the server before the list is redrawn by another action.
    await Promise.all(inlineSavers.map((saver) => saver.flush()));
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) return;
    if (root.getAttribute('aria-busy') === 'true') return;
    const data = new FormData(form);
    const action = data.get('action');
    const item = data.get('item');
    root.setAttribute('aria-busy', 'true');
    let response = null;
    let html;
    try {
      // Not form.action: the hidden input named "action" shadows that property.
      response = await fetch(form.getAttribute('action'), {
        method: 'POST',
        body: data,
        credentials: 'same-origin',
        headers: { 'X-JRC-Shotlist': '1' },
      });
      // Reading the body can fail too, after the server has already saved the change.
      html = await response.text();
    } catch (error) {
      response = null;
    }
    if (!response || response.status >= 500) {
      await reloadAfterUnsure(action, data);
      root.removeAttribute('aria-busy');
      return;
    }
    root.removeAttribute('aria-busy');
    const type = response.headers.get('content-type') || '';
    if (response.redirected || ![200, 400, 409].includes(response.status) || !type.includes('text/html')) {
      // The server refused without saving (sign-in redirect, 403, 404): show its page.
      window.location.assign(response.redirected ? response.url : `${window.location.pathname}?tab=shotlist`);
      return;
    }
    showList(html);
    restoreFocus(action, item);
  });
  initInline();
})();
