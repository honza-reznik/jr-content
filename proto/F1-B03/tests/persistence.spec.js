// AK4: verze jsou v PostgreSQL, ne v paměti. Test spustí vlastní server, uloží verzi,
// server ukončí, spustí znovu a ověří, že verze 1 i 2 zůstaly.
import { test, expect } from '@playwright/test'
import { spawn } from 'node:child_process'
import { createDocument, editorBox, clickEndOf, screenshot } from './helpers.js'

const PORT = 3101
const BASE = `http://127.0.0.1:${PORT}`

async function startServer() {
  const proc = spawn('node', ['server/index.js'], {
    env: { ...process.env, PORT: String(PORT), PGDATABASE: 'proto_test', PROTO_SEED: '0' },
    stdio: 'ignore',
  })
  for (let i = 0; i < 50; i++) {
    try {
      if ((await fetch(`${BASE}/api/health`)).ok) return proc
    } catch {}
    await new Promise((r) => setTimeout(r, 200))
  }
  proc.kill()
  throw new Error('Server nenaběhl.')
}

async function stopServer(proc) {
  const exited = new Promise((r) => proc.once('exit', r))
  proc.kill('SIGTERM')
  await exited
  await expect(fetch(`${BASE}/api/health`)).rejects.toThrow() // server opravdu neběží
}

test('verze zůstanou po restartu serveru', async ({ page, request }) => {
  let server = await startServer()
  let id
  try {
    id = await createDocument(request, 'Restart (test)', 'Text před restartem.', BASE)
    await page.goto(`${BASE}/?doc=${id}`)
    await page.getByRole('button', { name: 'Upravit text' }).click()
    await clickEndOf(page, 'Text před')
    await page.keyboard.type(' Doplněno ve verzi 2.')
    await page.getByRole('button', { name: 'Uložit' }).click()
    await expect(page.locator('#viewer-title')).toHaveText('Verze 2')
  } finally {
    await stopServer(server)
  }

  server = await startServer()
  try {
    await page.goto(`${BASE}/?doc=${id}`)
    await expect(page.locator('#viewer-title')).toHaveText('Verze 2')
    await expect(editorBox(page)).toHaveText('Text před restartem. Doplněno ve verzi 2.')
    await page.getByTestId('version-1').click()
    await expect(editorBox(page)).toHaveText('Text před restartem.')
    await screenshot(page, '06-po-restartu-serveru')
  } finally {
    await stopServer(server)
  }
})
