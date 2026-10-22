// AK5: HTTP API – přečíst poslední verzi, vytvořit novou verzi, ochrana proti přepsání cizí změny.
import { test, expect } from '@playwright/test'
import { createDocument } from './helpers.js'

test('API: poslední verze, nová verze, konflikty a chybný vstup', async ({ request, page }) => {
  const id = await createDocument(request, 'API (test)', '# Popisek\n\nPrvní verze popisku.')

  const latest = await request.get(`/api/documents/${id}/versions/latest`)
  expect(latest.status()).toBe(200)
  const v1 = await latest.json()
  expect(v1.number).toBe(1)
  expect(v1.status).toBe('closed')
  expect(v1.content.type).toBe('doc')
  expect(v1.markdown.trim()).toBe('# Popisek\n\nPrvní verze popisku.')

  // Nová verze z Markdownu (tak by ji zapsala AI).
  const created = await request.post(`/api/documents/${id}/versions`, {
    data: { markdown: '# Popisek\n\nDruhá verze, **lepší**.\n\n- [ ] doplnit hashtagy', base_version: 1 },
  })
  expect(created.status()).toBe(201)
  const v2 = await created.json()
  expect(v2.number).toBe(2)
  expect(v2.source).toBe('api')

  // Nová verze z JSON editoru.
  const fromJson = await request.post(`/api/documents/${id}/versions`, {
    data: { content: { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: 'Třetí.' }] }] } },
  })
  expect(fromJson.status()).toBe(201)
  expect((await fromJson.json()).number).toBe(3)

  // Zastaralý základ: volající vycházel z verze 1, ale poslední je 3.
  const stale = await request.post(`/api/documents/${id}/versions`, { data: { markdown: 'x', base_version: 1 } })
  expect(stale.status()).toBe(409)

  // Neplatný obsah.
  const bad = await request.post(`/api/documents/${id}/versions`, {
    data: { content: { type: 'doc', content: [{ type: 'neznamy_blok' }] } },
  })
  expect(bad.status()).toBe(400)
  expect((await request.post(`/api/documents/${id}/versions`, { data: {} })).status()).toBe(400)
  expect((await request.get(`/api/documents/999999/versions/latest`)).status()).toBe(404)

  // Když je verze rozpracovaná v editoru, API novou verzi nezaloží.
  expect((await request.post(`/api/documents/${id}/edit`)).status()).toBe(201)
  const busy = await request.post(`/api/documents/${id}/versions`, { data: { markdown: 'y' } })
  expect(busy.status()).toBe(409)
  // Poslední uložená verze je pořád 3.
  expect((await (await request.get(`/api/documents/${id}/versions/latest`)).json()).number).toBe(3)
  expect((await request.delete(`/api/documents/${id}/draft`)).status()).toBe(204)

  // Markdown jako čistý text.
  const md = await request.get(`/api/documents/${id}/versions/2?format=markdown`)
  expect(md.headers()['content-type']).toContain('text/markdown')
  expect((await md.text()).trim()).toBe('# Popisek\n\nDruhá verze, **lepší**.\n\n- [ ] doplnit hashtagy')

  // Verze z API jsou vidět i v editoru.
  await page.goto(`/?doc=${id}`)
  await expect(page.locator('#viewer-title')).toHaveText('Verze 3')
  await expect(page.getByTestId('version-2')).toContainText('přes API')
  await page.getByTestId('version-2').click()
  await expect(page.locator('#editor strong')).toHaveText('lepší')
})
