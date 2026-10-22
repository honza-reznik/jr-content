import { expect } from '@playwright/test'

export async function createDocument(request, title, markdown, baseURL) {
  const res = await request.post(`${baseURL ?? ''}/api/documents`, { data: { title, markdown } })
  expect(res.status()).toBe(201)
  return (await res.json()).id
}

export const editorBox = (page) => page.locator('#editor .ProseMirror')

/** Klikne na konec odstavce/položky s daným textem v editoru. */
export async function clickEndOf(page, text) {
  const block = editorBox(page).locator('p, h1, h2, h3').filter({ hasText: text }).first()
  await block.click()
  await page.keyboard.press('End')
  // ProseMirror převezme kurzor z prohlížeče až po události selectionchange (asynchronně).
  // Člověk je pomalejší, test ale musí počkat, jinak píše na staré místo.
  await expect
    .poll(() =>
      page.evaluate((t) => {
        const s = window.__editor.state.selection
        return s.empty && s.$from.parent.textContent.includes(t) && s.$from.parentOffset === s.$from.parent.content.size
      }, text),
    )
    .toBe(true)
}

/** Dvojklik na slovo a čekání, až ho editor opravdu vybere. */
export async function selectWord(page, locator, word) {
  await locator.dblclick({ position: { x: 5, y: 5 } })
  await expect.poll(() => page.evaluate(() => {
    const s = window.__editor.state.selection
    return window.__editor.state.doc.textBetween(s.from, s.to)
  })).toBe(word)
}

export async function screenshot(page, name) {
  await page.screenshot({ path: `screenshoty/${name}.png`, fullPage: true })
}
