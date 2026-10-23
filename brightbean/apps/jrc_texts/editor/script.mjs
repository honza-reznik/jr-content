import { Editor } from '@tiptap/core'
import { editorExtensions } from './extensions.mjs'

const panel = document.getElementById('script-panel')
const $ = name => document.getElementById(`script-${name}`)
let state, editing = false, finishing = false, fatal = false
let generation = 0, storedGeneration = 0, timer, pending = null, selected = null
const empty = { type: 'doc', content: [{ type: 'paragraph' }] }
const date = value => new Date(value).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })
const editor = new Editor({
  element: $('editor'), extensions: editorExtensions(), content: empty, editable: false,
  editorProps: { attributes: { role: 'textbox', 'aria-label': 'Script text', 'aria-multiline': 'true' } },
  onUpdate() {
    if (!editing) return
    generation++
    message('Changes waiting to save…')
    clearTimeout(timer)
    timer = setTimeout(autosave, 900)
  },
  onTransaction() {
    if (!editing) return
    for (const button of $('toolbar').querySelectorAll('[data-command]')) {
      const command = button.dataset.command
      if (command === 'undo' || command === 'redo') continue
      button.setAttribute('aria-pressed', String(command === 'h2'
        ? editor.isActive('heading', { level: 2 }) : editor.isActive(command)))
    }
  },
})

function message(text, error = false) {
  $('message').textContent = text
  $('message').classList.toggle('script-error', error)
}

async function api(data, version = null) {
  let response
  try {
    response = await fetch(panel.dataset.url + (version ? `?version=${version}` : ''), {
      method: data ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store',
      headers: data ? { 'Content-Type': 'application/json', 'X-CSRFToken': panel.querySelector('[name=csrfmiddlewaretoken]').value } : {},
      body: data ? JSON.stringify(data) : undefined,
    })
  } catch {
    throw new Error('Connection lost. Your text is still here. Retry when connected.')
  }
  const result = await response.json().catch(() => ({}))
  if (!response.ok || response.redirected || !('can_edit' in result)) {
    const error = new Error(result.error || (response.status === 403 || response.redirected
      ? 'Your access changed. Copy any unsaved text, then reload and sign in.'
      : 'The script could not be loaded or saved. Your text is still here. Please retry.'))
    error.fatal = [403, 404, 409].includes(response.status) || response.redirected
    throw error
  }
  return result
}

function controls() {
  $('edit').hidden = !state?.can_edit || editing || (!!state?.draft && !state.draft.recoverable) || !!selected
  $('resume').hidden = !state?.draft?.own || editing
  $('save').hidden = $('discard').hidden = $('toolbar').hidden = !editing
  $('save').disabled = $('discard').disabled = finishing || fatal
  $('edit').disabled = $('resume').disabled = finishing
  $('reload').disabled = finishing
  for (const button of $('versions').querySelectorAll('button')) button.disabled = editing || finishing
  // Changing control availability is not a document edit (including after an error).
  editor.setEditable(editing && !finishing, false)
  panel.classList.toggle('is-editing', editing)
}

function showState(next) {
  state = next
  editing = false
  fatal = false
  generation = storedGeneration = 0
  $('retry').hidden = true
  $('editor').setAttribute('aria-busy', 'false')
  editor.commands.setContent(state.shown?.content || empty, { emitUpdate: false })
  $('version').textContent = state.shown
    ? `Version ${state.shown.number} · ${state.shown.id === state.latest ? 'Latest saved' : 'Older version'} · ${date(state.shown.saved_at)} · ${state.shown.author}`
    : 'No script yet. Add an outline, talking points or a full script.'
  $('lock').hidden = !state.draft
  $('lock').textContent = state.draft ? `${state.draft.owner} is editing since ${date(state.draft.since)}. ${state.draft.own ? 'Your draft is available to resume.' : state.draft.recoverable ? 'This editor has left the workspace. Edit text starts a new draft from the latest saved version.' : 'You can read saved versions while they edit.'}` : ''
  $('versions').replaceChildren()
  for (const version of state.versions) {
    const button = document.createElement('button')
    button.type = 'button'
    button.textContent = `Version ${version.number}${version.id === state.latest ? ' · Latest' : ''}`
    const meta = document.createElement('span')
    meta.textContent = `${date(version.saved_at)} · ${version.author}`
    button.append(meta)
    button.setAttribute('aria-current', String(version.id === state.shown?.id))
    button.addEventListener('click', async () => {
      if (finishing || editing) return
      finishing = true
      controls()
      try {
        const choice = version.id === state.latest ? null : version.id
        const next = await api(null, choice)
        selected = choice
        showState(next)
        message(choice ? 'Reading an older saved version.' : 'Reading the latest saved version.')
      } catch (error) { message(error.message, true) }
      finally { finishing = false; controls() }
    })
    const item = document.createElement('li')
    item.append(button)
    $('versions').append(item)
  }
  controls()
}

function startEditing() {
  editing = true
  generation = storedGeneration = 0
  editor.commands.setContent(state.draft.content, { emitUpdate: false })
  $('version').textContent = `Draft · Version ${state.draft.number}`
  $('lock').textContent = `${state.draft.owner} is editing since ${date(state.draft.since)}. Your changes save to this draft automatically.`
  controls()
  editor.commands.focus('end')
  message('Editing. Changes save to your draft automatically; Save adds a saved version.')
}

function failed(error) {
  fatal = !!error.fatal
  $('retry').hidden = fatal || !editing
  message(error.message, true)
  controls()
}

async function autosave() {
  if (!editing || finishing || fatal || generation === storedGeneration) return
  if (pending) return pending
  const captured = generation
  const data = { action: 'autosave', version: state.draft.id, revision: state.draft.revision, content: editor.getJSON() }
  let success = false
  pending = (async () => {
    try {
      const next = await api(data)
      if (!next.draft?.own || next.draft.id !== data.version) {
        const error = new Error('This draft was saved or replaced in another tab. Copy any unsaved text, then reload.')
        error.fatal = true
        throw error
      }
      state.draft.revision = next.draft.revision
      storedGeneration = captured
      success = true
      $('retry').hidden = true
      message(captured === generation ? 'Draft saved. Use Save when ready.' : 'Changes waiting to save…')
    } catch (error) { failed(error) }
    finally {
      pending = null
      if (success && generation !== storedGeneration && !finishing) timer = setTimeout(autosave, 900)
    }
  })()
  return pending
}

async function finish(action) {
  if (finishing || fatal) return
  if (action === 'discard' && !window.confirm('Discard this draft? Your saved versions will stay available.')) return
  clearTimeout(timer)
  finishing = true
  controls()
  try {
    // Wait for any in-flight autosave before using its next revision.
    if (pending) await pending
    if (fatal) return
    const next = await api({ action, version: state.draft.id, revision: state.draft.revision,
      ...(action === 'save' ? { content: editor.getJSON() } : {}) })
    selected = null
    showState(next)
    message(action === 'save' ? 'Script saved.' : 'Draft discarded. Showing the latest saved version.')
  } catch (error) { failed(error) }
  finally { finishing = false; controls() }
}

$('edit').addEventListener('click', async () => {
  if (finishing) return
  finishing = true
  controls()
  try {
    state = await api({ action: 'edit', base: state.latest })
    showState(state)
    startEditing()
  } catch (error) { failed(error) }
  finally { finishing = false; controls(); if (editing) editor.commands.focus('end') }
})
$('resume').addEventListener('click', startEditing)
$('save').addEventListener('click', () => finish('save'))
$('discard').addEventListener('click', () => finish('discard'))
$('retry').addEventListener('click', autosave)
$('reload').addEventListener('click', async () => {
  if (finishing) return
  if (editing && generation !== storedGeneration && !window.confirm('Reload and lose changes that have not reached the server? Copy your text first if needed.')) return
  clearTimeout(timer)
  finishing = true
  controls()
  try {
    if (pending) await pending
    const next = await api()
    selected = null
    showState(next)
    message('Script reloaded.')
  } catch (error) { message(error.message, true) }
  finally { finishing = false; controls() }
})

const commands = {
  paragraph: chain => chain.setParagraph(), h2: chain => chain.toggleHeading({ level: 2 }),
  bold: chain => chain.toggleBold(), italic: chain => chain.toggleItalic(),
  bulletList: chain => chain.toggleBulletList(), orderedList: chain => chain.toggleOrderedList(),
  taskList: chain => chain.toggleTaskList(), undo: chain => chain.undo(), redo: chain => chain.redo(),
}
$('toolbar').addEventListener('mousedown', event => event.preventDefault())
$('toolbar').addEventListener('click', event => {
  const button = event.target.closest('[data-command]')
  if (button && editing && !finishing) commands[button.dataset.command](editor.chain().focus()).run()
})
window.addEventListener('beforeunload', event => {
  if (editing && (pending || generation !== storedGeneration)) { event.preventDefault(); event.returnValue = '' }
})

// Refresh other editors' presence without touching an active local draft.
setInterval(async () => {
  if (!state || editing || finishing || document.hidden) return
  finishing = true
  try { showState(await api(null, selected)) }
  catch (error) { message(error.message, true) }
  finally { finishing = false; controls() }
}, 15000)
api().then(next => { showState(next); message(next.can_edit ? 'Saved versions are kept when you edit.' : 'Read only. You can browse saved versions.') })
  .catch(error => { $('editor').setAttribute('aria-busy', 'false'); message(error.message, true) })
