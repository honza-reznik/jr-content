import { Editor } from '@tiptap/core'
import { Placeholder } from '@tiptap/extensions'
import { editorExtensions } from './extensions.mjs'
import { compareDocs } from './diff.mjs'

// Script and Concept tabs (P1-B08). P2-B08: the text is edited right in place and saves automatically
// (window.jrcAutosave, jrc_production/autosave.js); the server applies the version rule: the first edit
// starts a version, the same person on the same device keeps writing into it for 15 minutes after the
// last edit, otherwise a new version starts. This module runs once per page and mounts the editor
// whenever the tab's panel appears (the detail switches tabs without a page load).

const empty = { type: 'doc', content: [{ type: 'paragraph' }] }
const versionLabel = version => `V${version.number}${version.name ? ` – ${version.name}` : ''}`
const date = value => new Date(value).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })

function mount(panel) {
  const noun = panel.dataset.noun || 'script'
  const Noun = noun.charAt(0).toUpperCase() + noun.slice(1)
  const $ = name => panel.querySelector(`#script-${name}`)
  const autosave = window.jrcAutosave
  const device = autosave.deviceId()
  let state = null, selected = null, comparisonChoice = null, busy = false, destroyed = false
  // The saved version this editor writes on top of (null before the first one) and its revision.
  let base = null, revision = 0

  const editable = () => !!state?.can_edit && !selected && !destroyed
  // Created before the editor: TipTap may report its first transaction while being constructed.
  const saver = autosave.create({
    root: panel,
    status: $('status'),
    async send() {
      const result = await autosave.postJSON(panel.dataset.url, {
        action: 'autosave', base, revision, device, content: editor.getJSON(),
      }, panel)
      const saved = result.saved || {}
      if (saved.version) { base = saved.version.id; revision = saved.version.revision }
      state = result
      renderMeta()
      if (saved.concurrent) {
        notice(`${saved.concurrent.author} saved V${saved.concurrent.number} while you were editing. Your text is kept as its own version; compare the versions to combine them.`)
      }
      if (!saved.changed) return { message: 'No changes' }
      return { message: saved.created ? `Saved as V${saved.version.number}` : `Saved to V${saved.version.number}` }
    },
  })
  const editor = new Editor({
    element: $('editor'),
    extensions: [...editorExtensions(), Placeholder.configure({ placeholder: panel.dataset.empty || `Write the ${noun}…` })],
    content: empty,
    editable: false,
    editorProps: { attributes: { role: 'textbox', 'aria-label': `${Noun} text`, 'aria-multiline': 'true' } },
    onUpdate() { if (editable()) saver.changed() },
    onTransaction() {
      for (const button of $('toolbar').querySelectorAll('[data-command]')) {
        const command = button.dataset.command
        if (command === 'undo' || command === 'redo') continue
        button.setAttribute('aria-pressed', String(command === 'h2'
          ? editor.isActive('heading', { level: 2 }) : editor.isActive(command)))
      }
      for (const button of $('toolbar').querySelectorAll('[data-text-color]')) {
        button.setAttribute('aria-pressed', String(editor.isActive('textColor', { color: button.dataset.textColor })))
      }
      const clearColor = $('toolbar').querySelector('[data-text-color-clear]')
      if (clearColor) clearColor.setAttribute('aria-pressed', String(!editor.isActive('textColor')))
    },
  })

  function message(text, error = false) {
    $('message').textContent = text
    $('message').classList.toggle('script-error', error)
  }
  function notice(text) {
    $('lock').hidden = !text
    $('lock').textContent = text || ''
  }

  async function api(version = null, comparison = comparisonChoice) {
    const query = new URLSearchParams()
    if (version) query.set('version', version)
    if (comparison?.old) query.set('compare_old', comparison.old)
    if (comparison?.new) query.set('compare_new', comparison.new)
    const suffix = query.size ? `?${query}` : ''
    let response
    try {
      response = await fetch(panel.dataset.url + suffix, { credentials: 'same-origin', cache: 'no-store' })
    } catch {
      throw new Error(`Connection lost. The ${noun} could not be loaded.`)
    }
    const result = await response.json().catch(() => ({}))
    if (!response.ok || response.redirected || !('can_edit' in result)) {
      throw new Error(result.error || (response.status === 403 || response.redirected
        ? 'Your access changed. Reload the page and sign in.'
        : `The ${noun} could not be loaded. Please retry.`))
    }
    return result
  }

  function renderComparison(result) {
    const section = $('compare')
    const versions = state?.versions || []
    section.hidden = versions.length < 2
    const oldSelect = $('compare-old'), newSelect = $('compare-new')
    for (const select of [oldSelect, newSelect]) {
      select.replaceChildren()
      for (const version of versions) {
        const option = document.createElement('option')
        option.value = version.id
        option.textContent = versionLabel(version)
        select.append(option)
      }
    }
    if (versions.length < 2) return
    oldSelect.value = result?.old?.id || versions[1].id
    newSelect.value = result?.new?.id || versions[0].id
    $('compare-error').hidden = true
    if (!result) {
      $('diff').replaceChildren()
      $('compare-meta').textContent = 'Choose two saved versions to see what changed.'
      return
    }
    if (!result.old || !result.new) {
      $('diff').replaceChildren()
      $('compare-error').textContent = state.comparison_error || 'These versions could not be compared.'
      $('compare-error').hidden = false
      return
    }
    $('compare-meta').textContent = `Version ${result.old.number}: ${result.old.author}, ${date(result.old.saved_at)} → Version ${result.new.number}: ${result.new.author}, ${date(result.new.saved_at)}`
    if (state.comparison_error) {
      $('diff').replaceChildren()
      $('compare-error').textContent = state.comparison_error
      $('compare-error').hidden = false
      return
    }
    try {
      const rendered = compareDocs(result.old.content, result.new.content)
      $('diff').innerHTML = rendered.html
      $('diff').setAttribute('aria-label', `Added ${rendered.stats.added}, removed ${rendered.stats.removed}, changed ${rendered.stats.changed} blocks`)
    } catch (error) {
      $('diff').replaceChildren()
      $('compare-error').textContent = error instanceof RangeError ? error.message : 'These versions could not be compared.'
      $('compare-error').hidden = false
    }
  }

  function controls() {
    const on = editable()
    if (editor.isEditable !== on) editor.setEditable(on, false)
    $('toolbar').hidden = !on
    $('latest').hidden = !selected
    $('reload').disabled = busy
    $('compare-old').disabled = $('compare-new').disabled = busy
    for (const control of $('versions').querySelectorAll('button, input')) control.disabled = busy
  }

  // Version line, saved versions and comparison; never touches the text being typed.
  function renderMeta() {
    const shown = selected ? state.shown : state.versions.find(version => version.id === state.latest)
    $('version').textContent = shown
      ? `${versionLabel(shown)} · ${shown.id === state.latest ? 'Latest' : 'Older version, read only'} · ${date(shown.saved_at)} · ${shown.author}`
      : (state.can_edit ? `No ${noun} yet. Start typing below; it saves automatically.` : (panel.dataset.empty || `No ${noun} yet.`))
    if (!comparisonChoice) renderComparison(state.comparison)
    renderVersions()
    controls()
  }

  function renderVersions() {
    $('versions').replaceChildren()
    for (const version of state.versions) {
      const button = document.createElement('button')
      button.type = 'button'
      button.textContent = `${versionLabel(version)}${version.id === state.latest ? ' · Latest' : ''}`
      const meta = document.createElement('span')
      meta.textContent = `${date(version.saved_at)} · ${version.author}`
      button.append(meta)
      button.setAttribute('aria-current', String(selected ? version.id === selected : version.id === state.latest))
      button.addEventListener('click', () => openVersion(version.id === state.latest ? null : version.id))
      const item = document.createElement('li')
      item.append(button)
      if (state.can_edit) item.append(nameForm(version))
      $('versions').append(item)
    }
  }

  function nameForm(version) {
    const form = document.createElement('form')
    form.dataset.versionName = ''
    form.dataset.richName = ''
    const label = document.createElement('label')
    label.textContent = 'Version name (optional)'
    const input = document.createElement('input')
    input.name = 'name'; input.maxLength = 100; input.value = version.name
    input.setAttribute('aria-label', `Name of V${version.number}`)
    label.append(input)
    const save = document.createElement('button')
    save.type = 'submit'; save.textContent = 'Save name'
    form.append(label, save)
    form.addEventListener('submit', async event => {
      event.preventDefault()
      if (busy) return
      busy = true; controls()
      try {
        const data = new FormData()
        data.set('name', input.value)
        data.set('csrfmiddlewaretoken', panel.querySelector('[name=csrfmiddlewaretoken]').value)
        const prefix = panel.querySelector('[data-version-name-base]').dataset.versionNameBase
        const response = await fetch(`${prefix}versions/${version.id}/name/`, { method: 'POST', body: data, headers: { Accept: 'application/json' } })
        const result = await response.json().catch(() => ({}))
        if (!response.ok || response.redirected) throw new Error(result.error || 'Could not save the name. Check your access and retry.')
        state = await api(selected)
        renderMeta()
        message('Name saved.')
      } catch (error) { message(error.message, true) }
      finally { busy = false; controls() }
    })
    return form
  }

  // Show the latest version as the editable text (or the chosen older one, read only).
  function showText(next) {
    state = next
    const latest = state.versions.find(version => version.id === state.latest)
    base = latest ? latest.id : null
    revision = latest ? latest.revision : 0
    editor.commands.setContent(state.shown?.content || empty, { emitUpdate: false })
    $('editor').setAttribute('aria-busy', 'false')
    renderMeta()
  }

  async function openVersion(id) {
    if (busy || destroyed) return
    if (saver.dirty() && !(await saver.flush())) { message('Your latest changes are not saved yet. Try again when they are.', true); return }
    busy = true; controls()
    try {
      const next = await api(id)
      selected = id
      showText(next)
      message(id ? 'Reading an older saved version. Choose “Back to latest” to keep editing.'
        : (state.can_edit ? 'Click in the text to edit. Changes save automatically.' : 'Read only. You can browse saved versions.'))
    } catch (error) { message(error.message, true) }
    finally { busy = false; controls() }
  }

  async function loadComparison() {
    if (busy) return
    busy = true; controls()
    try {
      const choice = { old: $('compare-old').value, new: $('compare-new').value }
      state = await api(selected, choice)
      comparisonChoice = choice
      renderComparison(state.comparison)
      renderVersions()
    } catch (error) { message(error.message, true) }
    finally { busy = false; controls() }
  }

  const listeners = []
  const on = (element, type, handler) => { element.addEventListener(type, handler); listeners.push([element, type, handler]) }
  on($('copy'), 'click', () => window.jrcCopyText(editor.getText({ blockSeparator: '\n' }), $('copy')))
  on($('latest'), 'click', () => openVersion(null))
  on($('compare-old'), 'change', loadComparison)
  on($('compare-new'), 'change', loadComparison)
  on($('reload'), 'click', async () => {
    if (busy) return
    if (saver.dirty() && !(await saver.flush()) && !window.confirm('Reload and lose changes that have not reached the server? Copy your text first if needed.')) return
    busy = true; controls()
    try {
      selected = null
      comparisonChoice = null
      showText(await api())
      notice('')
      message(`${Noun} reloaded.`)
    } catch (error) { message(error.message, true) }
    finally { busy = false; controls() }
  })
  const commands = {
    paragraph: chain => chain.setParagraph(), h2: chain => chain.toggleHeading({ level: 2 }),
    bold: chain => chain.toggleBold(), italic: chain => chain.toggleItalic(),
    bulletList: chain => chain.toggleBulletList(), orderedList: chain => chain.toggleOrderedList(),
    taskList: chain => chain.toggleTaskList(), undo: chain => chain.undo(), redo: chain => chain.redo(),
  }
  on($('toolbar'), 'mousedown', event => {
    if (event.target.closest('button')) event.preventDefault()
  })
  on($('toolbar'), 'click', event => {
    const button = event.target.closest('[data-command]')
    if (button && editable()) commands[button.dataset.command](editor.chain().focus()).run()
    const color = event.target.closest('[data-text-color]')
    if (color && editable()) editor.chain().focus().setMark('textColor', { color: color.dataset.textColor }).run()
    if (event.target.closest('[data-text-color-clear]') && editable()) editor.chain().focus().unsetMark('textColor').run()
  })
  // "Use as script draft" leaves the page: the concept reaches the server first.
  const toScript = panel.querySelector('.script-to-script')
  if (toScript) on(toScript, 'submit', async event => {
    if (!saver.dirty()) return
    event.preventDefault()
    if (await saver.flush()) toScript.submit()
    else message('Your latest changes are not saved yet. Try again when they are.', true)
  })

  // Somebody else's newer version appears here while nobody types in this editor.
  const refresh = setInterval(async () => {
    if (!state || busy || destroyed || document.hidden || saver.dirty()) return
    try {
      const next = await api(selected)
      if (destroyed || saver.dirty()) return
      const latest = next.versions.find(version => version.id === next.latest)
      const changed = (latest?.id || null) !== base || (latest && latest.revision !== revision)
      if (!changed || selected) { state = next; renderMeta(); return }
      if ($('editor').contains(document.activeElement)) {
        notice(`${latest.author} saved ${versionLabel(latest)}. Reload the ${noun} to see it; your next change starts a new version.`)
        state = next
        renderMeta()
        return
      }
      showText(next)
    } catch { /* keep the last known state */ }
  }, 15000)

  function destroy() {
    destroyed = true
    clearInterval(refresh)
    saver.stop()
    for (const [element, type, handler] of listeners) element.removeEventListener(type, handler)
    editor.destroy()
  }

  api().then(next => {
    if (destroyed) return
    showText(next)
    message(next.can_edit ? 'Click in the text to edit. Changes save automatically.' : 'Read only. You can browse saved versions.')
  }).catch(error => { $('editor').setAttribute('aria-busy', 'false'); message(error.message, true) })
  return destroy
}

let mounted = null
function mountPresent() {
  const panel = document.getElementById('script-panel')
  if (!panel || panel.dataset.mounted || !window.jrcAutosave) return
  panel.dataset.mounted = '1'
  mounted = mount(panel)
}
document.addEventListener('jrc:panel-unload', () => {
  if (mounted) { mounted(); mounted = null }
})
document.addEventListener('jrc:panel-ready', mountPresent)
mountPresent()
