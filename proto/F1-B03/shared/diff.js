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
        let index = node.attrs?.start ?? 1
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
      const parts = isCode ? child.text.split(/(\n)/) : child.text.split(/(\s+|[.,;:!?()„“"])/)
      for (const part of parts) if (part) tokens.push({ t: part, marks })
    } else if (child.type === 'hardBreak') {
      tokens.push({ t: '\n', marks: [] })
    }
  }
  return tokens
}

const tokenKey = (tok) => tok.t + '\u0000' + tok.marks.join(',')

function blockAttrKey(b) {
  return [b.type, b.level, b.depth, b.quote, b.marker?.list ?? '', b.marker?.checked ?? ''].join('|')
}

function blockKey(b) {
  // Pořadové číslo položky číslovaného seznamu se nepočítá: vložení položky nemá označit všechny další.
  return blockAttrKey(b) + '#' + b.tokens.map(tokenKey).join('\u0001')
}

function similarity(a, b) {
  const ak = a.tokens.filter((t) => t.t.trim()).map((t) => t.t)
  const bk = b.tokens.filter((t) => t.t.trim()).map((t) => t.t)
  if (!ak.length && !bk.length) return a.type === b.type ? 1 : 0
  let common = 0
  for (const part of diffArrays(ak, bk)) if (!part.added && !part.removed) common += part.count
  return (2 * common) / (ak.length + bk.length)
}

/**
 * Vrátí seznam řádků výsledku: { status: 'same'|'added'|'removed'|'changed', block, oldBlock, runs }
 * a souhrn počtů.
 */
export function diffDocs(oldDoc, newDoc) {
  const oldBlocks = flatten(oldDoc)
  const newBlocks = flatten(newDoc)
  const parts = diffArrays(oldBlocks.map(blockKey), newBlocks.map(blockKey))
  const rows = []
  let oi = 0
  let ni = 0
  for (let p = 0; p < parts.length; p++) {
    const part = parts[p]
    if (!part.added && !part.removed) {
      for (let k = 0; k < part.count; k++) rows.push({ status: 'same', block: newBlocks[ni + k] })
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
    rows.push(...pairBlocks(removed, added))
  }
  const stats = { added: 0, removed: 0, changed: 0, same: 0 }
  for (const r of rows) stats[r.status]++
  return { rows, stats }
}

function pairBlocks(removed, added) {
  // Jednoduché párování po pořadí: blok se spáruje s nejbližším podobným blokem druhé strany.
  const out = []
  let ai = 0
  for (const oldBlock of removed) {
    let match = -1
    for (let j = ai; j < added.length; j++) {
      // Podobné bloky se porovnají po slovech: buď sdílí aspoň polovinu slov,
      // nebo mají stejný druh (např. obě položky checklistu) a aspoň jedno společné slovo.
      const sim = similarity(oldBlock, added[j])
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
    out.push({ status: 'changed', block: newBlock, oldBlock, runs: wordRuns(oldBlock.tokens, newBlock.tokens) })
  }
  for (; ai < added.length; ai++) out.push({ status: 'added', block: added[ai] })
  return out
}

function wordRuns(oldTokens, newTokens) {
  return groupRuns(rawWordRuns(oldTokens, newTokens))
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

function rawWordRuns(oldTokens, newTokens) {
  const parts = diffArrays(oldTokens.map(tokenKey), newTokens.map(tokenKey))
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
        const tag = MARK_TAGS[m]
        if (tag) html = `<${tag}>${html}</${tag}>`
      }
      return html
    })
    .join('')
}

function describeBlock(b) {
  if (b.type === 'heading') return `nadpis ${b.level}`
  if (b.marker?.list === 'bullet') return 'odrážka'
  if (b.marker?.list === 'ordered') return 'číslovaný seznam'
  if (b.marker?.list === 'task') return b.marker.checked ? 'úkol splněný' : 'úkol nesplněný'
  if (b.type === 'code') return 'kód'
  return 'odstavec'
}

function markerHtml(b) {
  if (!b.marker) return ''
  if (b.marker.list === 'bullet') return '<span class="d-marker">•</span>'
  if (b.marker.list === 'ordered') return `<span class="d-marker">${b.marker.index}.</span>`
  return `<span class="d-marker d-check">${b.marker.checked ? '☑' : '☐'}</span>`
}

function blockHtml(status, b, inner, note) {
  const tag = b.type === 'heading' ? `h${b.level}` : b.type === 'code' ? 'pre' : 'p'
  const classes = ['d-block', `d-${status}`, b.quote ? 'd-quote' : ''].filter(Boolean).join(' ')
  const indent = b.depth > 1 || (b.depth && !b.marker) ? ` style="margin-left:${(b.depth - (b.marker ? 1 : 0)) * 1.6}em"` : ''
  const noteHtml = note ? ` <span class="d-note">(${esc(note)})</span>` : ''
  if (b.type === 'hr') return `<div class="${classes}" data-status="${status}"><hr></div>`
  return `<div class="${classes}" data-status="${status}"${indent}>${markerHtml(b)}<${tag} class="d-text">${inner}</${tag}>${noteHtml}</div>`
}

export function renderDiffHtml(result) {
  return result.rows
    .map((row) => {
      const b = row.block
      if (row.status === 'same') return blockHtml('same', b, renderTokens(b.tokens))
      if (row.status === 'added') return blockHtml('added', b, `<ins>${renderTokens(b.tokens)}</ins>`, 'přidáno')
      if (row.status === 'removed') return blockHtml('removed', b, `<del>${renderTokens(b.tokens)}</del>`, 'smazáno')
      const inner = row.runs
        .map((r) =>
          r.kind === 'same'
            ? renderTokens(r.tokens)
            : `<${r.kind}>${renderTokens(r.tokens)}</${r.kind}>`,
        )
        .join('')
      const before = describeBlock(row.oldBlock)
      const after = describeBlock(b)
      const note = before !== after ? `${before} → ${after}` : row.oldBlock.depth !== b.depth ? 'změna odsazení' : ''
      return blockHtml('changed', b, inner, note)
    })
    .join('\n')
}

export function compareDocs(oldDoc, newDoc) {
  const result = diffDocs(oldDoc, newDoc)
  return { stats: result.stats, html: renderDiffHtml(result) }
}
