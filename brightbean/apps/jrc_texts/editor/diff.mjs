// Porovnání dvou verzí dokumentu Tiptap (JSON ProseMirroru).
//
// Postup:
// 1. Dokument se "zploští" na řadu bloků (nadpis, odstavec, položka seznamu, úkol checklistu…).
//    Každý blok si pamatuje typ, úroveň zanoření, značku seznamu a slova s formátováním.
// 2. Bloky obou verzí se porovnají algoritmem nejdelší společné posloupnosti (knihovna jsdiff).
// 3. Smazané a přidané bloky, které jsou si podobné, se spárují a porovnají po slovech.
//    Slovo se stejným textem, ale jiným formátováním (např. nově tučné) se bere jako změna.
// 4. Výsledek se vykreslí jako HTML se značkami <ins> (přidáno) a <del> (smazáno).
import { diffArrays } from 'diff'

export const MAX_COMPARE_BYTES = 250_000
const MAX_COMPARE_MS = 1000

// Všechny dílčí výpočty sdílejí jeden časový rozpočet, včetně párování bloků.
function boundedDiff(oldValues, newValues, deadline) {
  const timeout = deadline - performance.now()
  if (timeout <= 0) throw new RangeError('These versions are too complex to compare in the browser.')
  const parts = diffArrays(oldValues, newValues, { timeout, maxEditLength: 2000 })
  if (!parts) throw new RangeError('These versions are too complex to compare in the browser.')
  return parts
}

function jsonBytes(value) {
  return new TextEncoder().encode(JSON.stringify(value)).length
}

export function assertComparable(oldDoc, newDoc) {
  if (jsonBytes(oldDoc) + jsonBytes(newDoc) > MAX_COMPARE_BYTES) {
    throw new RangeError('These versions are too large to compare in the browser.')
  }
}

const TEXTBLOCKS = new Set(['paragraph', 'heading', 'codeBlock'])

export function flatten(doc) {
  const blocks = []
  const walk = (node, ctx) => {
    if (!node) return
    switch (node.type) {
      case 'doc':
        for (const child of node.content || []) walk(child, ctx)
        break
      case 'bulletList':
      case 'orderedList':
      case 'taskList': {
        let index = Number.isSafeInteger(node.attrs?.start) && node.attrs.start > 0 ? node.attrs.start : 1
        for (const item of node.content || []) {
          const holder = {
            marker: {
              list: node.type === 'bulletList' ? 'bullet' : node.type === 'orderedList' ? 'ordered' : 'task',
              index: index++,
              checked: node.type === 'taskList' ? Boolean(item.attrs?.checked) : null,
            },
          }
          walk(item, { ...ctx, depth: ctx.depth + 1, holder })
        }
        break
      }
      case 'listItem':
      case 'taskItem':
        for (const child of node.content || []) walk(child, ctx)
        break
      case 'blockquote':
        for (const child of node.content || []) walk(child, { ...ctx, quote: true, holder: null })
        break
      case 'horizontalRule':
        blocks.push({ type: 'hr', level: null, depth: ctx.depth, quote: ctx.quote, marker: null, tokens: [] })
        break
      default:
        if (TEXTBLOCKS.has(node.type)) {
          let marker = null
          if (ctx.holder && ctx.holder.marker) {
            marker = ctx.holder.marker
            ctx.holder.marker = null // značku dostane jen první blok položky, další jsou pokračování
          }
          const tokens = inlineTokens(node, node.type === 'codeBlock')
          // Prázdné odstavce (vč. prázdného řádku, který editor drží na konci dokumentu) se nepočítají.
          if (!marker && node.type === 'paragraph' && !tokens.some((t) => t.t.trim())) return
          blocks.push({
            type: node.type === 'codeBlock' ? 'code' : node.type,
            level: node.type === 'heading' ? node.attrs?.level ?? 1 : null,
            depth: ctx.depth,
            quote: Boolean(ctx.quote),
            marker,
            tokens,
          })
        } else {
          for (const child of node.content || []) walk(child, ctx)
        }
    }
  }
  walk(doc, { depth: 0, quote: false, holder: null })
  return blocks
}

function markNames(marks) {
  return (marks || []).map((m) => m.type).sort()
}

function inlineTokens(node, isCode) {
  const tokens = []
  for (const child of node.content || []) {
    if (child.type === 'text') {
      const marks = markNames(child.marks)
      const link = (child.marks || []).find((m) => m.type === 'link')
      const parts = isCode ? child.text.split(/(\n)/) : child.text.split(/(\s+|[.,;:!?()„“"])/)
      for (const part of parts) if (part) tokens.push({ t: part, marks, href: link?.attrs?.href ?? null })
    } else if (child.type === 'hardBreak') {
      tokens.push({ t: '\n', marks: [] })
    }
  }
  return tokens
}

const tokenKey = (tok) => tok.t + '\u0000' + tok.marks.join(',') + '\u0000' + (tok.href || '')

function blockAttrKey(b) {
  return [b.type, b.level, b.depth, b.quote, b.marker?.list ?? '', b.marker?.checked ?? ''].join('|')
}

function blockKey(b) {
  // Pořadové číslo položky číslovaného seznamu se nepočítá: vložení položky nemá označit všechny další.
  return blockAttrKey(b) + '#' + b.tokens.map(tokenKey).join('\u0001')
}

function similarity(a, b, deadline) {
  const ak = a.tokens.filter((t) => t.t.trim()).map((t) => t.t)
  const bk = b.tokens.filter((t) => t.t.trim()).map((t) => t.t)
  if (!ak.length && !bk.length) return a.type === b.type ? 1 : 0
  let common = 0
  for (const part of boundedDiff(ak, bk, deadline)) if (!part.added && !part.removed) common += part.count
  return (2 * common) / (ak.length + bk.length)
}

/**
 * Vrátí seznam řádků výsledku: { status: 'same'|'added'|'removed'|'changed', block, oldBlock, runs }
 * a souhrn počtů.
 */
export function diffDocs(oldDoc, newDoc) {
  assertComparable(oldDoc, newDoc)
  const deadline = performance.now() + MAX_COMPARE_MS
  const oldBlocks = flatten(oldDoc)
  const newBlocks = flatten(newDoc)
  const parts = boundedDiff(oldBlocks.map(blockKey), newBlocks.map(blockKey), deadline)
  const rows = []
  let oi = 0
  let ni = 0
  for (let p = 0; p < parts.length; p++) {
    const part = parts[p]
    if (!part.added && !part.removed) {
      for (let k = 0; k < part.count; k++) {
        const oldBlock = oldBlocks[oi + k]
        const block = newBlocks[ni + k]
        if (oldBlock.marker?.list === 'ordered' && oldBlock.marker.index !== block.marker?.index) {
          rows.push({ status: 'changed', block, oldBlock, runs: wordRuns(oldBlock.tokens, block.tokens, deadline) })
        } else rows.push({ status: 'same', block })
      }
      oi += part.count
      ni += part.count
      continue
    }
    // Úsek změn: posbírat sousední smazané a přidané bloky.
    const removed = []
    const added = []
    while (p < parts.length && (parts[p].added || parts[p].removed)) {
      const cur = parts[p]
      if (cur.removed) for (let k = 0; k < cur.count; k++) removed.push(oldBlocks[oi++])
      else for (let k = 0; k < cur.count; k++) added.push(newBlocks[ni++])
      p++
    }
    p--
    rows.push(...pairBlocks(removed, added, deadline))
  }
  const stats = { added: 0, removed: 0, changed: 0, same: 0 }
  for (const r of rows) stats[r.status]++
  return { rows, stats }
}

function pairBlocks(removed, added, deadline) {
  // Jednoduché párování po pořadí: blok se spáruje s nejbližším podobným blokem druhé strany.
  const out = []
  let ai = 0
  for (const oldBlock of removed) {
    let match = -1
    for (let j = ai; j < added.length; j++) {
      // Podobné bloky se porovnají po slovech: buď sdílí aspoň polovinu slov,
      // nebo mají stejný druh (např. obě položky checklistu) a aspoň jedno společné slovo.
      const sim = similarity(oldBlock, added[j], deadline)
      if (sim >= 0.5 || (sim > 0 && blockAttrKey(oldBlock) === blockAttrKey(added[j]))) {
        match = j
        break
      }
    }
    if (match === -1) {
      out.push({ status: 'removed', block: oldBlock })
      continue
    }
    for (; ai < match; ai++) out.push({ status: 'added', block: added[ai] })
    const newBlock = added[ai++]
    out.push({ status: 'changed', block: newBlock, oldBlock, runs: wordRuns(oldBlock.tokens, newBlock.tokens, deadline) })
  }
  for (; ai < added.length; ai++) out.push({ status: 'added', block: added[ai] })
  return out
}

function wordRuns(oldTokens, newTokens, deadline) {
  return groupRuns(rawWordRuns(oldTokens, newTokens, deadline))
}

/** Spojí změny oddělené jen mezerou: místo "~~u~~na ~~okna~~balkoně" vznikne "~~u okna~~ na balkoně". */
function groupRuns(runs) {
  const out = []
  let del = []
  let ins = []
  const flush = () => {
    if (del.length) out.push({ kind: 'del', tokens: del })
    if (ins.length) out.push({ kind: 'ins', tokens: ins })
    del = []
    ins = []
  }
  runs.forEach((run, i) => {
    const inChange = del.length || ins.length
    const between = i + 1 < runs.length && runs[i + 1].kind !== 'same'
    if (run.kind === 'same' && inChange && between && run.tokens.every((t) => !t.t.trim())) {
      del.push(...run.tokens)
      ins.push(...run.tokens)
    } else if (run.kind === 'del') del.push(...run.tokens)
    else if (run.kind === 'ins') ins.push(...run.tokens)
    else {
      flush()
      out.push(run)
    }
  })
  flush()
  return out
}

function rawWordRuns(oldTokens, newTokens, deadline) {
  const parts = boundedDiff(oldTokens.map(tokenKey), newTokens.map(tokenKey), deadline)
  const runs = []
  let oi = 0
  let ni = 0
  for (const part of parts) {
    if (part.removed) {
      runs.push({ kind: 'del', tokens: oldTokens.slice(oi, oi + part.count) })
      oi += part.count
    } else if (part.added) {
      runs.push({ kind: 'ins', tokens: newTokens.slice(ni, ni + part.count) })
      ni += part.count
    } else {
      runs.push({ kind: 'same', tokens: newTokens.slice(ni, ni + part.count) })
      oi += part.count
      ni += part.count
    }
  }
  return runs
}

// ---------- Vykreslení do HTML ----------

const esc = (s) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const MARK_TAGS = { bold: 'strong', italic: 'em', strike: 's', code: 'code', underline: 'u' }

function renderTokens(tokens) {
  return tokens
    .map((tok) => {
      let html = tok.t === '\n' ? '<br>' : esc(tok.t)
      for (const m of tok.marks) {
        const tag = Object.hasOwn(MARK_TAGS, m) ? MARK_TAGS[m] : null
        if (tag) html = `<${tag}>${html}</${tag}>`
      }
      if (tok.marks.includes('link') && safeHref(tok.href)) html = `<a href="${esc(tok.href)}" rel="noreferrer">${html}</a>`
      return html
    })
    .join('')
}

function safeHref(value) {
  return typeof value === 'string' && /^(https?:|mailto:)/i.test(value) && !/[\u0000-\u0020]/.test(value)
}

function describeBlock(b) {
  if (b.type === 'heading') return `Heading ${b.level}`
  if (b.marker?.list === 'bullet') return 'Bullet list'
  if (b.marker?.list === 'ordered') return 'Numbered list'
  if (b.marker?.list === 'task') return b.marker.checked ? 'Task checked' : 'Task unchecked'
  if (b.type === 'code') return 'Code block'
  return 'Paragraph'
}

function markerHtml(b) {
  if (!b.marker) return ''
  if (b.marker.list === 'bullet') return '<span class="d-marker">•</span>'
  if (b.marker.list === 'ordered') return `<span class="d-marker">${b.marker.index}.</span>`
  return `<span class="d-marker d-check">${b.marker.checked ? '☑' : '☐'}</span>`
}

function blockHtml(status, b, inner, note) {
  const level = Number.isInteger(b.level) && b.level >= 1 && b.level <= 6 ? b.level : 1
  const tag = b.type === 'heading' ? `h${level}` : b.type === 'code' ? 'pre' : 'p'
  const classes = ['d-block', `d-${status}`, b.quote ? 'd-quote' : ''].filter(Boolean).join(' ')
  const depth = Number.isInteger(b.depth) ? Math.max(0, Math.min(20, b.depth)) : 0
  const indent = depth > 1 || (depth && !b.marker) ? ` style="margin-left:${(depth - (b.marker ? 1 : 0)) * 1.6}em"` : ''
  const noteHtml = note ? ` <span class="d-note">(${esc(note)})</span>` : ''
  if (b.type === 'hr') return `<div class="${classes}" data-status="${status}"><hr></div>`
  return `<div class="${classes}" data-status="${status}"${indent}>${markerHtml(b)}<${tag} class="d-text">${inner}</${tag}>${noteHtml}</div>`
}

export function renderDiffHtml(result) {
  return result.rows
    .map((row) => {
      const b = row.block
      if (row.status === 'same') return blockHtml('same', b, renderTokens(b.tokens))
      if (row.status === 'added') return blockHtml('added', b, `<ins>${renderTokens(b.tokens)}</ins>`, 'Added')
      if (row.status === 'removed') return blockHtml('removed', b, `<del>${renderTokens(b.tokens)}</del>`, 'Removed')
      const inner = row.runs
        .map((r) =>
          r.kind === 'same'
            ? renderTokens(r.tokens)
            : `<${r.kind}>${renderTokens(r.tokens)}</${r.kind}>`,
        )
        .join('')
      const before = describeBlock(row.oldBlock)
      const after = describeBlock(b)
      const oldIndex = row.oldBlock.marker?.list === 'ordered' ? row.oldBlock.marker.index : null
      const newIndex = b.marker?.list === 'ordered' ? b.marker.index : null
      const note = before !== after ? `${before} → ${after}`
        : oldIndex !== null && newIndex !== null && oldIndex !== newIndex ? `number ${oldIndex} → ${newIndex}`
          : row.oldBlock.quote !== b.quote ? (b.quote ? 'Quote added' : 'Quote removed')
          : row.oldBlock.depth !== b.depth ? 'indentation changed' : ''
      return blockHtml('changed', b, inner, note)
    })
    .join('\n')
}

export function compareDocs(oldDoc, newDoc) {
  const result = diffDocs(oldDoc, newDoc)
  return { stats: result.stats, html: renderDiffHtml(result) }
}
