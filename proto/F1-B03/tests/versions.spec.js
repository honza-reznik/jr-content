// Hlavní scénář: Upravit text -> nová verze, uložení, zobrazení starších verzí, porovnání, API.
import { test, expect } from '@playwright/test'
import { createDocument, editorBox, clickEndOf, selectWord, screenshot } from './helpers.js'

const V1 = `# Reel o ranní rutině

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
`

test.describe.configure({ mode: 'serial' })

test('verze 1 → 2 → 3, zobrazení starší verze a porovnání', async ({ page, request }) => {
  const id = await createDocument(request, 'Scénář: ranní rutina (test)', V1)
  await page.goto(`/?doc=${id}`)

  // Verze 1 je jen ke čtení.
  await expect(page.locator('#viewer-title')).toHaveText('Verze 1')
  await expect(editorBox(page)).toHaveAttribute('contenteditable', 'false')
  await expect(page.getByTestId('version-1')).toBeVisible()
  await screenshot(page, '01-verze-1-jen-ke-cteni')

  // Upravit text -> verze 2 jako kopie verze 1.
  await page.getByRole('button', { name: 'Upravit text' }).click()
  await expect(page.locator('#viewer-title')).toHaveText('Verze 2 (rozpracovaná)')
  await expect(editorBox(page)).toHaveAttribute('contenteditable', 'true')
  await expect(editorBox(page)).toContainText('Krátké video na Instagram, 30 až 45 sekund.')

  await clickEndOf(page, 'Krátké video')
  await page.keyboard.type(' Hudba: klidná.')
  await selectWord(page, editorBox(page).getByText('Krátké video'), 'Krátké')
  await page.locator('#toolbar').getByRole('button', { name: 'Tučně' }).click()
  await clickEndOf(page, 'Jedna věc')
  await page.keyboard.press('Enter')
  await page.keyboard.type('Co mi pomáhá vstát')
  await editorBox(page).locator('li', { hasText: 'Nabít telefon' }).locator('input[type=checkbox]').check()
  await screenshot(page, '02-uprava-verze-2')
  await page.getByRole('button', { name: 'Uložit' }).click()
  await expect(page.locator('#viewer-title')).toHaveText('Verze 2')
  await expect(page.locator('#message')).toHaveText('Verze 2 uložena.')
  await expect(editorBox(page)).toHaveAttribute('contenteditable', 'false')
  await expect(editorBox(page).locator('strong')).toHaveText('Krátké')

  // Verze 3.
  await page.getByRole('button', { name: 'Upravit text' }).click()
  await expect(page.locator('#viewer-title')).toHaveText('Verze 3 (rozpracovaná)')
  await expect(editorBox(page)).toContainText('Co mi pomáhá vstát') // kopie verze 2, ne verze 1
  await clickEndOf(page, 'Protažení u okna')
  await page.keyboard.press('Shift+Home')
  await page.keyboard.type('Protažení na balkoně')
  await clickEndOf(page, 'Proč vstávám')
  await page.keyboard.press('Shift+Home')
  await page.keyboard.press('Backspace')
  await page.keyboard.press('Backspace') // prázdná položka se smaže
  await page.getByRole('button', { name: 'Uložit' }).click()
  await expect(page.locator('#viewer-title')).toHaveText('Verze 3')

  // Všechny tři verze zůstaly a jdou zobrazit.
  for (const n of [1, 2, 3]) await expect(page.getByTestId(`version-${n}`)).toBeVisible()
  await page.getByTestId('version-1').click()
  await expect(page.locator('#viewer-title')).toHaveText('Verze 1')
  await expect(editorBox(page)).toContainText('Protažení u okna')
  await expect(editorBox(page)).toContainText('Proč vstávám v šest')
  await expect(editorBox(page)).not.toContainText('Hudba')
  await expect(editorBox(page).locator('li', { hasText: 'Nabít telefon' }).locator('input')).not.toBeChecked()
  await screenshot(page, '03-zobrazena-starsi-verze-1')
  await page.getByTestId('version-2').click()
  await expect(editorBox(page)).toContainText('Hudba: klidná.')
  await expect(editorBox(page)).toContainText('Protažení u okna')

  // Porovnání verze 1 a 3.
  await page.locator('#cmp-from').selectOption('1')
  await page.locator('#cmp-to').selectOption('3')
  await page.getByRole('button', { name: 'Porovnat' }).click()
  const diff = page.locator('#diff')
  await expect(diff.locator('ins', { hasText: 'Hudba' })).toBeVisible()
  await expect(diff.locator('ins strong')).toHaveText('Krátké') // změna formátu: nově tučně
  await expect(diff.locator('del', { hasText: 'Krátké' })).toBeVisible()
  await expect(diff.locator('ins', { hasText: 'balkoně' })).toBeVisible()
  await expect(diff.locator('del', { hasText: 'okna' })).toBeVisible()
  await expect(diff.locator('[data-status=removed]', { hasText: 'Proč vstávám v šest' })).toBeVisible()
  await expect(diff.locator('[data-status=added]', { hasText: 'Co mi pomáhá vstát' })).toBeVisible()
  await expect(diff.locator('[data-status=changed]', { hasText: 'Nabít telefon' })).toContainText(
    'úkol nesplněný → úkol splněný',
  )
  await expect(diff.locator('[data-status=same]', { hasText: 'Káva a plán dne' })).toBeVisible()
  await screenshot(page, '04-porovnani-verze-1-a-3')

  // Uložené verze se nezměnily (kontrola přes API).
  const v1 = await (await request.get(`/api/documents/${id}/versions/1?format=markdown`)).text()
  expect(v1.trim()).toBe(V1.trim())
})

test('zahození rozpracované verze nechá poslední uloženou', async ({ page, request }) => {
  const id = await createDocument(request, 'Zahození (test)', 'Původní text.')
  await page.goto(`/?doc=${id}`)
  await page.getByRole('button', { name: 'Upravit text' }).click()
  await clickEndOf(page, 'Původní')
  await page.keyboard.type(' Změna, která se zahodí.')
  page.once('dialog', (d) => d.accept())
  await page.getByRole('button', { name: 'Zahodit změny' }).click()
  await expect(page.locator('#viewer-title')).toHaveText('Verze 1')
  await expect(page.getByTestId('version-2')).toHaveCount(0)
  await expect(editorBox(page)).toHaveText('Původní text.')
})

test('rozpracovaná verze přežije obnovení stránky', async ({ page, request }) => {
  const id = await createDocument(request, 'Obnovení stránky (test)', 'Text.')
  await page.goto(`/?doc=${id}`)
  await page.getByRole('button', { name: 'Upravit text' }).click()
  await expect(page.locator('#viewer-title')).toHaveText('Verze 2 (rozpracovaná)')
  await page.reload()
  await expect(page.locator('#viewer-title')).toHaveText('Verze 2 (rozpracovaná)')
  await expect(page.getByRole('button', { name: 'Uložit' })).toBeVisible()
})
