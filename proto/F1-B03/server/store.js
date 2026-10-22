// Ukládání dokumentů a jejich verzí v PostgreSQL.
//
// Pravidla verzí:
// - Verze se číslují 1, 2, 3… v rámci dokumentu a nikdy se nemažou ani nepřepisují, když jsou uzavřené.
// - "Upravit text" založí rozpracovanou verzi (status 'draft') jako kopii poslední uzavřené verze.
// - Uložení rozpracovanou verzi uzavře (status 'closed'). Zahození ji smaže (číslo se uvolní).
// - Dokument má nejvýš jednu rozpracovanou verzi (hlídá to unikátní index v databázi).
import pg from 'pg'

export const pool = new pg.Pool({ max: 5 })

export class ConflictError extends Error {}
export class NotFoundError extends Error {}

export async function migrate() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS documents (
      id          serial PRIMARY KEY,
      title       text NOT NULL,
      created_at  timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE IF NOT EXISTS document_versions (
      id           serial PRIMARY KEY,
      document_id  int NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
      number       int NOT NULL,
      status       text NOT NULL CHECK (status IN ('draft', 'closed')),
      content      jsonb NOT NULL,
      source       text NOT NULL DEFAULT 'editor' CHECK (source IN ('editor', 'api')),
      created_at   timestamptz NOT NULL DEFAULT now(),
      closed_at    timestamptz,
      UNIQUE (document_id, number)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS document_versions_one_draft
      ON document_versions (document_id) WHERE status = 'draft';
  `)
}

const META = `id, document_id, number, status, source, created_at, closed_at`

async function tx(fn) {
  const client = await pool.connect()
  try {
    await client.query('BEGIN')
    const result = await fn(client)
    await client.query('COMMIT')
    return result
  } catch (err) {
    await client.query('ROLLBACK')
    throw err
  } finally {
    client.release()
  }
}

async function lockDocument(client, documentId) {
  const { rows } = await client.query('SELECT id, title FROM documents WHERE id = $1 FOR UPDATE', [documentId])
  if (!rows.length) throw new NotFoundError('Dokument neexistuje.')
  return rows[0]
}

export async function listDocuments() {
  const { rows } = await pool.query(`
    SELECT d.id, d.title, d.created_at,
           (SELECT max(number) FROM document_versions v WHERE v.document_id = d.id AND v.status = 'closed') AS latest_version
    FROM documents d ORDER BY d.id`)
  return rows
}

export async function createDocument(title, content) {
  return tx(async (client) => {
    const { rows } = await client.query('INSERT INTO documents (title) VALUES ($1) RETURNING id, title, created_at', [title])
    const doc = rows[0]
    await client.query(
      `INSERT INTO document_versions (document_id, number, status, content, source, closed_at)
       VALUES ($1, 1, 'closed', $2, 'api', now())`,
      [doc.id, content],
    )
    return doc
  })
}

export async function getDocument(documentId) {
  const { rows } = await pool.query('SELECT id, title, created_at FROM documents WHERE id = $1', [documentId])
  if (!rows.length) throw new NotFoundError('Dokument neexistuje.')
  const versions = await pool.query(
    `SELECT ${META} FROM document_versions WHERE document_id = $1 ORDER BY number`,
    [documentId],
  )
  return { ...rows[0], versions: versions.rows }
}

export async function getVersion(documentId, number) {
  const { rows } = await pool.query(
    `SELECT ${META}, content FROM document_versions WHERE document_id = $1 AND number = $2`,
    [documentId, number],
  )
  if (!rows.length) throw new NotFoundError(`Verze ${number} neexistuje.`)
  return rows[0]
}

export async function getLatestVersion(documentId) {
  const { rows } = await pool.query(
    `SELECT ${META}, content FROM document_versions
     WHERE document_id = $1 AND status = 'closed' ORDER BY number DESC LIMIT 1`,
    [documentId],
  )
  if (!rows.length) throw new NotFoundError('Dokument nemá žádnou uloženou verzi.')
  return rows[0]
}

/** "Upravit text": vrátí rozpracovanou verzi; když žádná není, založí ji jako kopii poslední. */
export async function startEdit(documentId) {
  return tx(async (client) => {
    await lockDocument(client, documentId)
    const draft = await client.query(
      `SELECT ${META}, content FROM document_versions WHERE document_id = $1 AND status = 'draft'`,
      [documentId],
    )
    if (draft.rows.length) return { version: draft.rows[0], created: false }
    const { rows } = await client.query(
      `INSERT INTO document_versions (document_id, number, status, content, source)
       SELECT document_id, number + 1, 'draft', content, 'editor' FROM document_versions
       WHERE document_id = $1 ORDER BY number DESC LIMIT 1
       RETURNING ${META}, content`,
      [documentId],
    )
    return { version: rows[0], created: true }
  })
}

/** Uloží obsah rozpracované verze a uzavře ji. */
export async function closeDraft(documentId, content) {
  return tx(async (client) => {
    await lockDocument(client, documentId)
    const { rows } = await client.query(
      `UPDATE document_versions SET content = $2, status = 'closed', closed_at = now()
       WHERE document_id = $1 AND status = 'draft' RETURNING ${META}, content`,
      [documentId, content],
    )
    if (!rows.length) throw new ConflictError('Dokument nemá rozpracovanou verzi.')
    return rows[0]
  })
}

export async function discardDraft(documentId) {
  return tx(async (client) => {
    await lockDocument(client, documentId)
    const { rowCount } = await client.query(
      `DELETE FROM document_versions WHERE document_id = $1 AND status = 'draft'`,
      [documentId],
    )
    if (!rowCount) throw new ConflictError('Dokument nemá rozpracovanou verzi.')
  })
}

/**
 * Nová uzavřená verze najednou (pro API, např. pro AI).
 * baseVersion: číslo verze, ze které volající vycházel; když mezitím vznikla novější, vrátí konflikt.
 */
export async function createVersion(documentId, content, baseVersion) {
  return tx(async (client) => {
    await lockDocument(client, documentId)
    const { rows: last } = await client.query(
      `SELECT number, status FROM document_versions WHERE document_id = $1 ORDER BY number DESC LIMIT 1`,
      [documentId],
    )
    const latest = last[0]
    if (latest?.status === 'draft') {
      throw new ConflictError(`Verze ${latest.number} je právě rozpracovaná v editoru. Nejdřív ji ulož nebo zahoď.`)
    }
    if (baseVersion != null && latest && Number(baseVersion) !== latest.number) {
      throw new ConflictError(`Poslední verze je ${latest.number}, ne ${baseVersion}. Načti ji a změnu udělej znovu.`)
    }
    const { rows } = await client.query(
      `INSERT INTO document_versions (document_id, number, status, content, source, closed_at)
       VALUES ($1, $2, 'closed', $3, 'api', now()) RETURNING ${META}, content`,
      [documentId, (latest?.number ?? 0) + 1, content],
    )
    return rows[0]
  })
}
