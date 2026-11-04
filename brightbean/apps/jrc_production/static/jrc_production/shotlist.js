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
    let response;
    try {
      // Not form.action: the hidden input named "action" shadows that property.
      response = await fetch(form.getAttribute('action'), {
        method: 'POST',
        body: data,
        credentials: 'same-origin',
        headers: { 'X-JRC-Shotlist': '1' },
      });
    } catch (error) {
      root.removeAttribute('aria-busy');
      form.submit();
      return;
    }
    root.removeAttribute('aria-busy');
    const type = response.headers.get('content-type') || '';
    if (response.redirected || ![200, 400, 409].includes(response.status) || !type.includes('text/html')) {
      // Permission or session problems (e.g. a redirect to sign-in): let the browser show the server's page.
      form.submit();
      return;
    }
    root.innerHTML = await response.text();
    const progress = root.querySelector('[data-shotlist-progress]');
    if (status && progress) status.textContent = `Shotlist ${progress.textContent.trim()}`;
    restoreFocus(action, item);
  });
})();
