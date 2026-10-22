// Webová stránka prototypu: seznam verzí, editor Tiptap a porovnání.
import { Editor } from '@tiptap/core'
import { editorExtensions } from '../shared/extensions.js'

const $ = (id) => document.getElementById(id)
const state = { docId: null, doc: null, shown: null, editing: false }

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 204) return null
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `Chyba ${res.status}`)
  return data
}

const editor = new Editor({
  element: $('editor'),
  extensions: editorExtensions(),
  editable: false,
  content: '',
  onTransaction: () => updateToolbar(),
})

const COMMANDS = {
  paragraph: (c) => c.setParagraph(),
  h1: (c) => c.toggleHeading({ level: 1 }),
  h2: (c) => c.toggleHeading({ level: 2 }),
  h3: (c) => c.toggleHeading({ level: 3 }),
  bold: (c) => c.toggleBold(),
  italic: (c) => c.toggleItalic(),
  bulletList: (c) => c.toggleBulletList(),
  orderedList: (c) => c.toggleOrderedList(),
  taskList: (c) => c.toggleTaskList(),
  undo: (c) => c.undo(),
  redo: (c) => c.redo(),
}
const ACTIVE = {
  paragraph: () => editor.isActive('paragraph') && !editor.isActive('listItem') && !editor.isActive('taskItem'),
  h1: () => editor.isActive('heading', { level: 1 }),
  h2: () => editor.isActive('heading', { level: 2 }),
  h3: () => editor.isActive('heading', { level: 3 }),
  bold: () => editor.isActive('bold'),
  italic: () => editor.isActive('italic'),
  bulletList: () => editor.isActive('bulletList'),
  orderedList: () => editor.isActive('orderedList'),
  taskList: () => editor.isActive('taskList'),
}

$('toolbar').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-cmd]')
  if (!btn || !state.editing) return
  // focus() v Tiptapu vrací kurzor se zpožděním (requestAnimationFrame); když editor fokus má, nevolá se.
  const chain = editor.view.hasFocus() ? editor.chain() : editor.chain().focus()
  COMMANDS[btn.dataset.cmd](chain).run()
})
// Tlačítka panelu nesmí brát editoru kurzor.
$('toolbar').addEventListener('mousedown', (e) => e.preventDefault())

function updateToolbar() {
  if (!state.editing) return
  for (const btn of $('toolbar').querySelectorAll('button[data-cmd]')) {
    const active = ACTIVE[btn.dataset.cmd]
    if (active) btn.setAttribute('aria-pressed', String(active()))
  }
}

function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString('cs-CZ', { dateStyle: 'short', timeStyle: 'short' }) : ''
}

function message(text, isError = false) {
  $('message').textContent = text
  $('message').classList.toggle('error', isError)
}

function renderVersions() {
  const list = $('versions')
  list.innerHTML = ''
  for (const v of state.doc.versions) {
    const li = document.createElement('li')
    const btn = document.createElement('button')
    btn.type = 'button'
    btn.dataset.testid = `version-${v.number}`
    btn.setAttribute('aria-current', String(state.shown?.number === v.number))
    btn.disabled = state.editing
    const status = v.status === 'draft' ? '<span class="draft">rozpracovaná</span>' : 'uložená'
    const source = v.source === 'api' ? ' · přes API' : ''
    btn.innerHTML = `Verze ${v.number}<span class="meta">${status}${source} · ${fmtTime(v.closed_at || v.created_at)}</span>`
    btn.addEventListener('click', () => showVersion(v.number))
    li.append(btn)
    list.append(li)
  }
  const closed = state.doc.versions.filter((v) => v.status === 'closed')
  for (const [id, pick] of [['cmp-from', closed.length - 2], ['cmp-to', closed.length - 1]]) {
    const sel = $(id)
    const prev = sel.value
    sel.innerHTML = closed.map((v) => `<option value="${v.number}">Verze ${v.number}</option>`).join('')
    if (prev && closed.some((v) => String(v.number) === prev)) sel.value = prev
    else if (closed[Math.max(pick, 0)]) sel.value = String(closed[Math.max(pick, 0)].number)
  }
}

function setEditing(on) {
  state.editing = on
  editor.setEditable(on)
  $('editor').classList.toggle('editing', on)
  $('toolbar').hidden = !on
  $('btn-save').hidden = !on
  $('btn-discard').hidden = !on
  $('btn-edit').hidden = on
  for (const b of $('versions').querySelectorAll('button')) b.disabled = on
  $('doc-select').disabled = on
}

function showContent(version) {
  state.shown = version
  editor.commands.setContent(version.content, { emitUpdate: false })
  const draft = version.status === 'draft'
  $('viewer-title').textContent = `Verze ${version.number}${draft ? ' (rozpracovaná)' : ''}`
  $('viewer-info').textContent = draft
    ? `Kopie verze ${version.number - 1}. Uložením se verze ${version.number} uzavře, starší verze zůstanou beze změny.`
    : `Uloženo ${fmtTime(version.closed_at)}${version.source === 'api' ? ' přes API' : ''}. Uložená verze se už nemění.`
  setEditing(draft)
  renderVersions()
}

async function showVersion(number) {
  const v = await api('GET', `/api/documents/${state.docId}/versions/${number}`)
  showContent(v)
  message('')
}

async function loadDocument(id, numberToShow) {
  state.docId = id
  state.doc = await api('GET', `/api/documents/${id}`)
  const url = new URL(location.href)
  url.searchParams.set('doc', id)
  history.replaceState(null, '', url)
  const draft = state.doc.versions.find((v) => v.status === 'draft')
  const latest = state.doc.versions.filter((v) => v.status === 'closed').at(-1)
  await showVersion(numberToShow ?? draft?.number ?? latest.number)
  $('diff').innerHTML = ''
}

$('btn-edit').addEventListener('click', async () => {
  try {
    const v = await api('POST', `/api/documents/${state.docId}/edit`)
    state.doc = await api('GET', `/api/documents/${state.docId}`)
    showContent(v)
    editor.commands.focus('end')
    message(`Založena verze ${v.number} jako kopie poslední verze. Uprav text a ulož.`)
  } catch (err) {
    message(err.message, true)
  }
})

$('btn-save').addEventListener('click', async () => {
  try {
    const v = await api('PUT', `/api/documents/${state.docId}/draft`, { content: editor.getJSON() })
    state.doc = await api('GET', `/api/documents/${state.docId}`)
    showContent(v)
    message(`Verze ${v.number} uložena.`)
  } catch (err) {
    message(err.message, true)
  }
})

$('btn-discard').addEventListener('click', async () => {
  if (!confirm('Zahodit rozpracovanou verzi? Změny se ztratí.')) return
  try {
    await api('DELETE', `/api/documents/${state.docId}/draft`)
    await loadDocument(state.docId)
    message('Rozpracovaná verze zahozena.')
  } catch (err) {
    message(err.message, true)
  }
})

$('btn-compare').addEventListener('click', async () => {
  try {
    const from = $('cmp-from').value
    const to = $('cmp-to').value
    const r = await api('GET', `/api/documents/${state.docId}/compare?from=${from}&to=${to}`)
    $('diff').innerHTML = r.html
    $('diff').dataset.stats = JSON.stringify(r.stats)
    message(`Porovnání verze ${r.from} s verzí ${r.to}: ${r.stats.changed} změněných, ${r.stats.added} přidaných a ${r.stats.removed} smazaných bloků.`)
  } catch (err) {
    message(err.message, true)
  }
})

$('doc-select').addEventListener('change', (e) => loadDocument(Number(e.target.value)))

async function init() {
  const docs = await api('GET', '/api/documents')
  $('doc-select').innerHTML = docs.map((d) => `<option value="${d.id}">${d.title.replace(/</g, '&lt;')}</option>`).join('')
  const wanted = Number(new URL(location.href).searchParams.get('doc'))
  const id = docs.some((d) => d.id === wanted) ? wanted : docs[0]?.id
  if (!id) {
    $('viewer-title').textContent = 'Žádný dokument'
    return
  }
  $('doc-select').value = String(id)
  await loadDocument(id)
}

init().catch((err) => message(err.message, true))
window.__editor = editor // pro automatické testy
