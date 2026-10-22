// Připraví prázdnou testovací databázi (PGDATABASE, výchozí proto_test). Ukázková data v "proto" nemění.
import pg from 'pg'

const name = process.env.PGDATABASE || 'proto_test'
if (!/^[a-z_][a-z0-9_]*$/.test(name) || name === 'proto') throw new Error(`Nepovolené jméno testovací databáze: ${name}`)

const admin = new pg.Client({ database: 'proto' })
await admin.connect()
const { rowCount } = await admin.query('SELECT 1 FROM pg_database WHERE datname = $1', [name])
if (!rowCount) await admin.query(`CREATE DATABASE ${name}`)
await admin.end()

const db = new pg.Client({ database: name })
await db.connect()
await db.query('DROP TABLE IF EXISTS document_versions, documents')
await db.end()
console.log(`Testovací databáze ${name} připravena.`)
