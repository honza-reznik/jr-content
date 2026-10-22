// HTTP server prototypu: webová stránka editoru + jednoduché API nad verzemi.
// Připojení k databázi se bere z proměnných PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE.
import express from 'express'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import * as store from './store.js'
import { ContentError, validateContent, markdownToContent, contentToMarkdown } from './content.js'
import { compareDocs } from '../shared/diff.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const PORT = Number(process.env.PORT || 3000)

const SEED_MARKDOWN = `# Reel o ranní rutině

Krátké video na Instagram, 30 až 45 sekund.

## Scény

1. Budík a první doušek vody
2. Protažení u okna
3. Káva a plán dne

## Co říct

- Proč vstávám v šest
- Jedna věc, kterou dělám každé ráno

## Před natáčením

- [ ] Nabít telefon
- [ ] Uklidit kuchyň
- [x] Domluvit termín s Ellou
`

const app = express()
app.use(express.json({ limit: '1mb' }))
app.use(express.static(path.join(here, '..', 'public')))

const wrap = (fn) => (req, res, next) => Promise.resolve(fn(req, res)).catch(next)

const docId = (req) => {
  const id = Number(req.params.id)
  if (!Number.isInteger(id) || id < 1) throw new store.NotFoundError('Dokument neexistuje.')
  return id
}

/** Obsah z těla požadavku: buď "content" (JSON editoru), nebo "markdown" (text). */
function contentFromBody(body) {
  if (body?.content !== undefined) return validateContent(body.content)
  if (body?.markdown !== undefined) return markdownToContent(body.markdown)
  throw new ContentError('Chybí obsah: pošli pole "content" (JSON editoru) nebo "markdown".')
}

function versionOut(v, format) {
  if (format === 'markdown') return contentToMarkdown(v.content)
  return { ...v, markdown: v.content ? contentToMarkdown(v.content) : undefined }
}

function sendVersion(req, res, v, status = 200) {
  if (req.query.format === 'markdown') {
    res.status(status).type('text/markdown; charset=utf-8').send(versionOut(v, 'markdown'))
  } else {
    res.status(status).json(versionOut(v))
  }
}

app.get('/api/health', wrap(async (_req, res) => {
  await store.pool.query('SELECT 1')
  res.json({ ok: true })
}))

app.get('/api/documents', wrap(async (_req, res) => res.json(await store.listDocuments())))

app.post('/api/documents', wrap(async (req, res) => {
  const title = String(req.body?.title || '').trim()
  if (!title) throw new ContentError('Chybí název dokumentu (pole "title").')
  const doc = await store.createDocument(title, contentFromBody(req.body))
  res.status(201).json(await store.getDocument(doc.id))
}))

app.get('/api/documents/:id', wrap(async (req, res) => res.json(await store.getDocument(docId(req)))))

app.get('/api/documents/:id/versions', wrap(async (req, res) => {
  res.json((await store.getDocument(docId(req))).versions)
}))

// AK5: přečíst poslední (uzavřenou) verzi. ?format=markdown vrátí čistý text.
app.get('/api/documents/:id/versions/latest', wrap(async (req, res) => {
  sendVersion(req, res, await store.getLatestVersion(docId(req)))
}))

app.get('/api/documents/:id/versions/:number', wrap(async (req, res) => {
  const number = Number(req.params.number)
  if (!Number.isInteger(number)) throw new store.NotFoundError('Verze neexistuje.')
  sendVersion(req, res, await store.getVersion(docId(req), number))
}))

// AK5: vytvořit novou uzavřenou verzi. Tělo: { markdown | content, base_version? }.
app.post('/api/documents/:id/versions', wrap(async (req, res) => {
  const content = contentFromBody(req.body)
  sendVersion(req, res, await store.createVersion(docId(req), content, req.body?.base_version), 201)
}))

// Editor: "Upravit text" -> rozpracovaná verze jako kopie poslední.
app.post('/api/documents/:id/edit', wrap(async (req, res) => {
  const { version, created } = await store.startEdit(docId(req))
  sendVersion(req, res, version, created ? 201 : 200)
}))

// Editor: "Uložit" -> uloží obsah a verzi uzavře.
app.put('/api/documents/:id/draft', wrap(async (req, res) => {
  sendVersion(req, res, await store.closeDraft(docId(req), contentFromBody(req.body)))
}))

app.delete('/api/documents/:id/draft', wrap(async (req, res) => {
  await store.discardDraft(docId(req))
  res.status(204).end()
}))

// Porovnání dvou verzí: ?from=1&to=3
app.get('/api/documents/:id/compare', wrap(async (req, res) => {
  const id = docId(req)
  const from = await store.getVersion(id, Number(req.query.from))
  const to = await store.getVersion(id, Number(req.query.to))
  res.json({ from: from.number, to: to.number, ...compareDocs(from.content, to.content) })
}))

app.use('/api', (_req, res) => res.status(404).json({ error: 'Neznámá adresa API.' }))

app.use((err, _req, res, _next) => {
  if (err instanceof ContentError) return res.status(400).json({ error: err.message })
  if (err instanceof store.NotFoundError) return res.status(404).json({ error: err.message })
  if (err instanceof store.ConflictError) return res.status(409).json({ error: err.message })
  if (err.type === 'entity.parse.failed') return res.status(400).json({ error: 'Tělo požadavku není platný JSON.' })
  console.error(err)
  res.status(500).json({ error: 'Chyba serveru.' })
})

async function main() {
  await store.migrate()
  if (process.env.PROTO_SEED !== '0' && (await store.listDocuments()).length === 0) {
    await store.createDocument('Scénář: Reel o ranní rutině', markdownToContent(SEED_MARKDOWN))
    console.log('Založen ukázkový dokument.')
  }
  app.listen(PORT, '0.0.0.0', () => console.log(`Prototyp F1-B03 poslouchá na portu ${PORT}`))
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
