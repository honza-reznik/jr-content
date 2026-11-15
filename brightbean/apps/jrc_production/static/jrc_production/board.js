(() => {
  'use strict';
  const root = document.querySelector('[data-board-actions]');
  if (!root) return;
  const results = document.getElementById('board-results');
  const storageKey = 'jrc-board-action-results';
  let busy = false;
  function report(rows) {
    results.replaceChildren();
    const heading = document.createElement('p');
    heading.textContent = `${rows.filter(row => row.ok).length} succeeded · ${rows.filter(row => !row.ok).length} failed`;
    const list = document.createElement('ul');
    for (const row of rows) {
      const item = document.createElement('li');
      item.textContent = `${row.title || 'Content'}: ${row.message}`;
      list.append(item);
    }
    results.append(heading, list);
    results.hidden = false;
    results.focus();
  }
  try {
    const previous = sessionStorage.getItem(storageKey);
    sessionStorage.removeItem(storageKey);
    if (previous) report(JSON.parse(previous));
  } catch { /* Browser storage may be disabled. */ }
  async function send(items, action, fields = {}) {
    if (busy || !items.length) return;
    busy = true;
    root.setAttribute('aria-busy', 'true');
    const rows = [];
    try {
      for (let offset = 0; offset < items.length; offset += 100) {
        const chunk = items.slice(offset, offset + 100);
        const response = await fetch(root.dataset.boardActions, {
          method: 'POST', credentials: 'same-origin', signal: AbortSignal.timeout(90000),
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value },
          body: JSON.stringify({ action, items: chunk.map(card => ({ id: card.dataset.post, expected_stage: card.dataset.stage })), ...fields }),
        });
        if (!response.ok) throw new Error('The action could not be completed. Refresh the page and check the items before trying again.');
        const data = await response.json();
        for (const row of data.results) {
          const card = chunk.find(item => item.dataset.post === row.id);
          row.title = card?.querySelector('h3, .board-trash-title strong')?.textContent || 'Content';
          rows.push(row);
        }
      }
      report(rows);
      if (rows.some(row => row.ok)) {
        try { sessionStorage.setItem(storageKey, JSON.stringify(rows)); } catch { return; }
        location.reload();
      }
    } catch (error) {
      report([...rows, { ok: false, message: error.message || 'Connection lost. Refresh the board to check the result.' }]);
    } finally {
      busy = false;
      root.removeAttribute('aria-busy');
    }
  }
  if (root.hasAttribute('data-trash-page')) {
    root.addEventListener('click', event => {
      const item = event.target.closest('[data-post]');
      if (event.target.closest('[data-restore]')) send([item], 'restore');
      if (event.target.closest('[data-purge]') && confirm('Permanently delete this content and its saved work? This cannot be undone.')) send([item], 'purge');
      if (event.target.closest('#empty-trash') && confirm('Permanently delete all content you may delete in Trash? This cannot be undone.')) {
        send([...root.querySelectorAll('[data-post]:has([data-purge])')], 'purge');
      }
    });
    return;
  }
  const grid = root.querySelector('.board-grid');
  const cards = () => [...grid.querySelectorAll('.board-card')];
  const visible = () => cards().filter(card => card.querySelector('.board-select') && !card.hidden && card.getClientRects().length);
  const selected = () => cards().filter(card => card.querySelector('.board-select')?.checked);
  let lastSelected = null;
  function selection() {
    for (const card of cards()) card.classList.toggle('is-selected', card.querySelector('.board-select')?.checked);
    const count = document.getElementById('selection-count');
    if (count) count.textContent = `${selected().length} selected`;
    root.querySelectorAll('[data-bulk-move], [data-bulk-approve], [data-bulk-trash], #clear-selection').forEach(button => { button.disabled = !selected().length; });
    const moveButton = root.querySelector('[data-bulk-move]');
    if (moveButton) moveButton.disabled = !selected().some(card => card.querySelector('[name=stage]'));
    const approveButton = root.querySelector('[data-bulk-approve]');
    if (approveButton) approveButton.disabled = !selected().some(card => card.dataset.canApprove === '1');
    const trashButton = root.querySelector('[data-bulk-trash]');
    if (trashButton) trashButton.disabled = !selected().some(card => card.dataset.canTrash === '1');
  }
  grid.addEventListener('click', event => {
    if (!event.target.matches('.board-select')) return;
    const card = event.target.closest('.board-card');
    const all = visible();
    if (event.shiftKey && all.includes(lastSelected)) {
      const [start, end] = [all.indexOf(card), all.indexOf(lastSelected)].sort((a, b) => a - b);
      all.slice(start, end + 1).forEach(item => { item.querySelector('.board-select').checked = event.target.checked; });
    }
    lastSelected = card;
    selection();
  });
  document.getElementById('select-visible')?.addEventListener('click', () => { visible().forEach(card => { card.querySelector('.board-select').checked = true; }); selection(); });
  document.getElementById('clear-selection')?.addEventListener('click', () => { selected().forEach(card => { card.querySelector('.board-select').checked = false; }); selection(); });
  function filter() {
    const needle = document.getElementById('quick-search').value.trim().toLocaleLowerCase();
    for (const card of cards()) {
      card.hidden = !card.querySelector('h3').textContent.toLocaleLowerCase().includes(needle);
      if (card.hidden && card.querySelector('.board-select')) card.querySelector('.board-select').checked = false;
    }
    for (const column of grid.querySelectorAll('.board-column')) {
      const count = [...column.querySelectorAll('.board-card')].filter(card => !card.hidden).length;
      column.querySelector('[data-stage-count]').textContent = count;
      column.classList.toggle('is-empty', count === 0);
      const option = document.querySelector(`#mobile-stage option[value="${column.dataset.stage}"]`);
      option.textContent = `${option.dataset.label} (${count})`;
    }
    document.getElementById('board-no-results').hidden = cards().some(card => !card.hidden);
    selection();
  }
  document.getElementById('quick-search').addEventListener('input', filter);
  document.addEventListener('board:changed', filter);
  const dialog = document.getElementById('board-action-dialog');
  const stage = document.getElementById('action-stage');
  let actionItems = [], actionKind = 'move';
  function actionFields() {
    document.getElementById('action-comment-wrap').hidden = stage.value !== 'approved'
      || (actionKind !== 'approve' && actionItems.every(card => card.dataset.canApprove !== '1'));
    document.getElementById('action-date-wrap').hidden = stage.value !== 'ready';
    document.getElementById('action-date').required = stage.value === 'ready';
    document.getElementById('board-action-hint').textContent = stage.value === 'approved'
      ? (actionKind === 'approve' || actionItems.every(card => card.dataset.canApprove === '1') ? 'Confirm approval. Each item follows this client’s review steps. Items you cannot approve will stay in place with an explanation.' : 'Eligible items will be sent for approval; no review step is skipped.')
      : 'Each item is checked separately. Items that cannot move will stay in their current stage.';
  }
  function openAction(items, target = '', kind = 'move') {
    if (!items.length || busy) return;
    actionItems = items;
    actionKind = kind;
    const choices = items.length === 1 ? [...items[0].querySelectorAll('[name=stage] option')].map(option => option.value) : null;
    for (const option of stage.options) option.disabled = choices !== null && !choices.includes(option.value);
    // A drag may deliberately target an unavailable stage: the server explains the refusal.
    if (target) stage.value = target;
    else stage.value = [...stage.options].find(option => !option.disabled)?.value || 'idea';
    stage.disabled = Boolean(target);
    document.getElementById('action-date').value = '';
    document.getElementById('action-comment').value = '';
    document.getElementById('board-action-title').textContent = `${stage.value === 'approved' ? 'Approve / request approval' : 'Move content'} · ${items.length} item${items.length === 1 ? '' : 's'}`;
    actionFields();
    dialog.showModal();
  }
  stage.addEventListener('change', actionFields);
  document.getElementById('cancel-board-action').addEventListener('click', () => dialog.close());
  document.getElementById('board-action-form').addEventListener('submit', event => {
    event.preventDefault();
    dialog.close();
    send(actionItems, actionKind, { stage: stage.value, scheduled_at: document.getElementById('action-date').value, comment: document.getElementById('action-comment').value });
  });
  root.querySelector('[data-bulk-move]')?.addEventListener('click', () => openAction(selected()));
  root.querySelector('[data-bulk-approve]')?.addEventListener('click', () => openAction(selected(), 'approved', 'approve'));
  root.querySelector('[data-bulk-trash]')?.addEventListener('click', () => { if (confirm(`Move ${selected().length} selected items to Trash?`)) send(selected(), 'trash'); });
  grid.addEventListener('submit', event => {
    if (!event.target.closest('.board-move')) return;
    event.preventDefault();
    const card = event.target.closest('.board-card');
    const target = event.target.elements.stage.value;
    if (target === 'approved') openAction([card], target);
    else send([card], 'move', { stage: target, scheduled_at: event.target.elements.scheduled_at?.value || '' });
  });
  const menu = document.getElementById('card-action-menu');
  let menuSource = null;
  function closeMenu(focus = false) { menu.hidden = true; if (focus) menuSource?.focus(); }
  function showMenu(card, x, y) {
    menu.replaceChildren();
    menuSource = card.querySelector('[data-card-menu]');
    const add = (label, callback) => {
      const button = document.createElement('button');
      button.type = 'button'; button.role = 'menuitem'; button.textContent = label;
      button.addEventListener('click', () => { closeMenu(); callback(); });
      menu.append(button);
    };
    const link = card.querySelector('.board-card-link');
    const open = document.createElement('a'); open.href = link.href; open.textContent = 'Open content'; open.role = 'menuitem'; menu.append(open);
    const moves = [...card.querySelectorAll('[name=stage] option')];
    for (const option of moves) add(option.value === 'approved' ? (card.dataset.canApprove === '1' ? 'Approve…' : 'Request approval…') : `Move to ${option.textContent}…`, () => openAction([card], option.value));
    if (card.dataset.canTrash === '1') add('Move to Trash', () => { if (confirm('Move this content to Trash?')) send([card], 'trash'); });
    add('Copy link', async () => {
      try { await navigator.clipboard.writeText(link.href); report([{ ok: true, title: link.textContent, message: 'Link copied.' }]); }
      catch { prompt('Copy this content link:', link.href); }
    });
    menu.hidden = false; menu.style.left = '8px'; menu.style.top = '8px';
    const box = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(x, innerWidth - box.width - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, innerHeight - box.height - 8))}px`;
    menu.querySelector('[role=menuitem]').focus();
  }
  grid.addEventListener('contextmenu', event => {
    const card = event.target.closest('.board-card');
    if (card) { event.preventDefault(); showMenu(card, event.clientX, event.clientY); }
  });
  grid.addEventListener('click', event => {
    const button = event.target.closest('[data-card-menu]');
    if (button) { const box = button.getBoundingClientRect(); showMenu(button.closest('.board-card'), box.left, box.bottom); }
  });
  document.addEventListener('click', event => { if (!event.target.closest('#card-action-menu, [data-card-menu]')) closeMenu(); });
  document.addEventListener('keydown', event => {
    if (menu.hidden) return;
    if (event.key === 'Escape') { closeMenu(true); event.preventDefault(); }
    if (event.key === 'Tab') closeMenu();
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      const entries = [...menu.querySelectorAll('[role=menuitem]')];
      const index = entries.indexOf(document.activeElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? entries.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + entries.length) % entries.length;
      entries[next].focus(); event.preventDefault();
    }
  });
  let dragged = null;
  function highlight(column) {
    grid.querySelectorAll('.is-drop-target').forEach(item => item.classList.remove('is-drop-target'));
    column?.classList.add('is-drop-target');
  }
  function drop(card, column) {
    if (!column || column.dataset.stage === card.dataset.stage) return;
    const target = column.dataset.stage;
    if (['approved', 'ready'].includes(target)) openAction([card], target);
    else send([card], 'move', { stage: target });
  }
  grid.addEventListener('dragstart', event => {
    const card = event.target.closest('.board-card');
    if (!card || card.draggable === false || event.target.closest('input, select, button, details')) { event.preventDefault(); return; }
    dragged = card; card.classList.add('is-dragging');
    event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', card.dataset.post);
  });
  grid.addEventListener('dragover', event => {
    if (!dragged) return;
    event.preventDefault(); highlight(event.target.closest('.board-column'));
    const box = grid.getBoundingClientRect();
    if (event.clientX > box.right - 45) grid.scrollLeft += 18;
    if (event.clientX < box.left + 45) grid.scrollLeft -= 18;
  });
  grid.addEventListener('drop', event => { if (dragged) { event.preventDefault(); drop(dragged, event.target.closest('.board-column')); } });
  grid.addEventListener('dragend', () => { dragged?.classList.remove('is-dragging'); dragged = null; highlight(null); });
  let touch = null, hold = null, held = false;
  grid.addEventListener('pointerdown', event => {
    if (event.pointerType === 'mouse') return;
    const card = event.target.closest('.board-card');
    if (!card) return;
    if (event.target.closest('[data-drag-handle]')) {
      event.preventDefault(); event.target.setPointerCapture(event.pointerId);
      touch = { card, x: event.clientX, y: event.clientY }; card.classList.add('is-dragging');
    } else if (!event.target.closest('input, button, details')) {
      hold = { x: event.clientX, y: event.clientY, timer: setTimeout(() => { held = true; showMenu(card, event.clientX, event.clientY); }, 550) };
    }
  });
  grid.addEventListener('pointermove', event => {
    if (hold && Math.hypot(event.clientX - hold.x, event.clientY - hold.y) > 10) { clearTimeout(hold.timer); hold = null; }
    if (!touch) return;
    event.preventDefault(); touch.x = event.clientX; touch.y = event.clientY;
    highlight(document.elementFromPoint(touch.x, touch.y)?.closest('.board-column'));
    if (touch.y > innerHeight - 70) window.scrollBy(0, 35);
    if (touch.y < 70) window.scrollBy(0, -35);
  });
  function endTouch(event) {
    if (hold) { clearTimeout(hold.timer); hold = null; }
    if (touch) {
      touch.card.classList.remove('is-dragging');
      if (event.type === 'pointerup') drop(touch.card, document.elementFromPoint(touch.x, touch.y)?.closest('.board-column'));
      touch = null; highlight(null);
    }
  }
  grid.addEventListener('pointerup', endTouch);
  grid.addEventListener('pointercancel', endTouch);
  grid.addEventListener('click', event => { if (held) { event.preventDefault(); event.stopImmediatePropagation(); held = false; } }, true);
  selection();
})();
