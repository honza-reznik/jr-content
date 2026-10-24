import assert from 'node:assert/strict'
import test from 'node:test'
import { compareDocs, diffDocs } from './diff.mjs'

const text = (value, marks = []) => ({ type: 'text', text: value, ...(marks.length ? { marks } : {}) })
const para = (...content) => ({ type: 'paragraph', content })
const doc = (...content) => ({ type: 'doc', content })
const list = (type, content, attrs = {}) => ({ type, attrs, content })
const item = (content, attrs = {}) => ({ type: 'listItem', attrs, content: [para(text(content))] })

test('identical, empty, and empty paragraphs produce no changes', () => {
  assert.deepEqual(diffDocs(doc(para()), doc()).stats, { added: 0, removed: 0, changed: 0, same: 0 })
  assert.deepEqual(diffDocs(doc(para(text('same'))), doc(para(), para(text('same')))).stats,
    { added: 0, removed: 0, changed: 0, same: 1 })
})

test('new bold word, task check, and paragraph-to-heading are visible', () => {
  const bold = compareDocs(doc(para(text('A word'))), doc(para(text('A '), text('word', [{ type: 'bold' }]))))
  assert.match(bold.html, /<del>word<\/del>/)
  assert.match(bold.html, /<ins><strong>word<\/strong><\/ins>/)

  const tasks = (checked) => doc(list('taskList', [{ type: 'taskItem', attrs: { checked }, content: [para(text('Do this'))] }]))
  assert.match(compareDocs(tasks(false), tasks(true)).html, /Task unchecked → Task checked/)

  const heading = compareDocs(doc(para(text('Title'))), doc({ type: 'heading', attrs: { level: 2 }, content: [text('Title')] }))
  assert.match(heading.html, /Paragraph → Heading 2/)
})

test('ordered list starting number is compared and moved blocks remain represented', () => {
  const old = doc(list('orderedList', [item('First'), item('Second')], { start: 5 }))
  const next = doc(list('orderedList', [item('First'), item('Second')], { start: 1 }))
  const result = compareDocs(old, next)
  assert.match(result.html, /number 5 → 1/)
  assert.match(result.html, /number 6 → 2/)
  const moved = diffDocs(doc(para(text('A')), para(text('B'))), doc(para(text('B')), para(text('A'))))
  assert.ok(moved.stats.added + moved.stats.removed + moved.stats.changed > 0)
})

test('document text is escaped and unsafe link schemes are shown as text', () => {
  const payload = doc(para(text('<script>alert(1)</script> <img onerror="x">')))
  const result = compareDocs(doc(), payload)
  assert.match(result.html, /&lt;script&gt;/)
  assert.match(result.html, /&lt;img onerror=&quot;x&quot;&gt;/)
  assert.doesNotMatch(result.html, /<script>|<img\b/)

  const linkDoc = (href) => doc(para({
    type: 'text', text: 'link', marks: [{ type: 'link', attrs: { href } }],
  }))
  assert.doesNotMatch(compareDocs(doc(), linkDoc('javascript:alert(1)')).html, /href=/)
  assert.match(compareDocs(doc(), linkDoc('https://example.invalid/?a=1&b=2')).html,
    /href="https:\/\/example\.invalid\/\?a=1&amp;b=2"/)
  for (const href of ['data:text/html,<script>alert(1)</script>', '//example.invalid', 'java\nscript:alert(1)']) {
    assert.doesNotMatch(compareDocs(doc(), linkDoc(href)).html, /href=/)
  }
  for (const payload of ['<script>alert(1)</script>', '<img/onerror="alert(1)">']) {
    const html = compareDocs(doc(), linkDoc(`https://example.invalid/"${payload}`)).html
    assert.doesNotMatch(html, /<script>|<img\b|href="[^"]*"</)
    assert.match(html, /&lt;/)
  }
  assert.doesNotMatch(compareDocs(doc(), linkDoc('https://example.invalid/"<img onerror="alert(1)">')).html, /href=/)
  assert.match(compareDocs(doc(), linkDoc('mailto:hello@example.invalid')).html, /href="mailto:/)

  const poisonedList = doc(list('orderedList', [item('safe text')], { start: '<img src=x onerror=alert(1)>' }))
  const markerHtml = compareDocs(doc(), poisonedList).html
  assert.match(markerHtml, /<span class="d-marker">1\.<\/span>/)
  assert.doesNotMatch(markerHtml, /onerror|<img\b/)
})

test('oversized documents are rejected before diffing', () => {
  const huge = doc(para(text('x'.repeat(130_000))))
  assert.throws(() => compareDocs(huge, huge), RangeError)
})

test('long texts finish or report complexity within a bounded time', () => {
  const words = Array.from({ length: 6000 }, (_, i) => `word${i}`).join(' ')
  const started = performance.now()
  const similar = compareDocs(doc(para(text(words))), doc(para(text(`${words} added`))))
  assert.match(similar.html, /<ins> added<\/ins>/)
  const unrelated = Array.from({ length: 6000 }, (_, i) => `other${i}`).join(' ')
  assert.throws(() => compareDocs(doc(para(text(words))), doc(para(text(unrelated)))), /too complex/)
  assert.ok(performance.now() - started < 3000, 'Long documents must not freeze the browser')
})
