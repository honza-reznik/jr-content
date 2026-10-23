// Kontrola a převody obsahu na serveru: JSON editoru <-> Markdown.
import { getSchema } from '@tiptap/core'
import { MarkdownManager } from '@tiptap/markdown'
import { editorExtensions } from './extensions.mjs'

const extensions = editorExtensions()
const schema = getSchema(extensions)
const markdown = new MarkdownManager({ extensions })

export class ContentError extends Error {}

/** Ověří, že JSON odpovídá schématu editoru, a vrátí ho normalizovaný. */
export function validateContent(json) {
  if (!json || typeof json !== 'object' || json.type !== 'doc') {
    throw new ContentError('Obsah musí být dokument editoru ({"type":"doc", ...}).')
  }
  try {
    const node = schema.nodeFromJSON(json)
    node.check()
    return node.toJSON()
  } catch (err) {
    throw new ContentError('Obsah neodpovídá schématu editoru: ' + err.message)
  }
}

export function markdownToContent(text) {
  if (typeof text !== 'string') throw new ContentError('Pole markdown musí být text.')
  if (!text.trim()) return validateContent({ type: 'doc', content: [{ type: 'paragraph' }] })
  return validateContent(markdown.parse(text))
}

export function contentToMarkdown(json) {
  return markdown.serialize(json)
}
