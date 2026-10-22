// AK1: nadpisy, odrážky, číslovaný seznam a checklist přes panel nástrojů; uloží se a jdou přečíst přes API.
import { test, expect } from '@playwright/test'
import { createDocument, editorBox, clickEndOf, screenshot } from './helpers.js'

test('formátování přes panel nástrojů', async ({ page, request }) => {
  const id = await createDocument(request, 'Formátování (test)', 'Úvod.')
  await page.goto(`/?doc=${id}`)
  await page.getByRole('button', { name: 'Upravit text' }).click()
  const tool = (name) => page.locator('#toolbar').getByRole('button', { name, exact: true })

  await clickEndOf(page, 'Úvod.')
  await page.keyboard.press('Enter')
  await tool('Nadpis 2').click()
  await page.keyboard.type('Plán natáčení')
  await expect(tool('Nadpis 2')).toHaveAttribute('aria-pressed', 'true')
  await page.keyboard.press('Enter')

  await tool('• Odrážky').click()
  await page.keyboard.type('první odrážka')
  await page.keyboard.press('Enter')
  await page.keyboard.type('druhá odrážka')
  await page.keyboard.press('Enter')
  await page.keyboard.press('Enter') // prázdná položka ukončí seznam

  await tool('1. Číslování').click()
  await page.keyboard.type('jedna')
  await page.keyboard.press('Enter')
  await page.keyboard.type('dvě')
  await page.keyboard.press('Enter')
  await page.keyboard.press('Enter')

  await tool('☑ Checklist').click()
  await page.keyboard.type('koupit rekvizity')
  await page.keyboard.press('Enter')
  await page.keyboard.type('nabít světlo')
  await editorBox(page).locator('li', { hasText: 'koupit rekvizity' }).locator('input[type=checkbox]').check()

  await expect(editorBox(page).locator('h2')).toHaveText('Plán natáčení')
  await expect(editorBox(page).locator('ul:not([data-type]) > li')).toHaveCount(2)
  await expect(editorBox(page).locator('ol > li')).toHaveCount(2)
  await expect(editorBox(page).locator('ul[data-type=taskList] > li')).toHaveCount(2)
  await screenshot(page, '05-formatovani-v-editoru')

  await page.getByRole('button', { name: 'Uložit' }).click()
  await expect(page.locator('#viewer-title')).toHaveText('Verze 2')

  const md = await (await request.get(`/api/documents/${id}/versions/latest?format=markdown`)).text()
  expect(md.trim()).toBe(
    [
      'Úvod.',
      '',
      '## Plán natáčení',
      '',
      '- první odrážka',
      '- druhá odrážka',
      '',
      '1. jedna',
      '2. dvě',
      '',
      '- [x] koupit rekvizity',
      '- [ ] nabít světlo',
    ].join('\n'),
  )
})
