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

  function showList(html) {
    root.innerHTML = html;
    const progress = root.querySelector('[data-shotlist-progress]');
    if (status && progress) status.textContent = `Shotlist ${progress.textContent.trim()}`;
  }

  // After an unknown outcome (no answer, or a server error) never send the change again: an
  // add or a move that was saved would be applied twice. Load the current list instead (GET).
  async function reloadAfterUnsure(action, data) {
    try {
      const response = await fetch(`${window.location.pathname}?tab=shotlist`, { credentials: 'same-origin' });
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

  root.addEventListener('submit', async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !root.contains(form)) return;
    event.preventDefault();
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
})();
