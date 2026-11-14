// Společná sada rozšíření editoru: stejná v prohlížeči (editor) i na serveru (kontrola dat, Markdown).
import StarterKit from '@tiptap/starter-kit'
import { TaskList, TaskItem } from '@tiptap/extension-list'
import { Mark } from '@tiptap/core'

const TEXT_COLORS = Object.freeze([
  '#b91c1c', '#c2410c', '#a16207', '#15803d',
  '#0f766e', '#0369a1', '#4338ca', '#7e22ce',
])

const TextColor = Mark.create({
  name: 'textColor',
  addAttributes() {
    return {
      color: {
        default: null,
        parseHTML: element => TEXT_COLORS.includes(element.getAttribute('data-jrc-text-color'))
          ? element.getAttribute('data-jrc-text-color') : null,
        renderHTML: attributes => TEXT_COLORS.includes(attributes.color)
          ? { 'data-jrc-text-color': attributes.color, style: `color: ${attributes.color}` } : {},
      },
    }
  },
  parseHTML() {
    return [{ tag: 'span[data-jrc-text-color]' }]
  },
  renderHTML({ HTMLAttributes }) {
    return ['span', HTMLAttributes, 0]
  },
  renderMarkdown(node, helpers) {
    const color = TEXT_COLORS.includes(node.attrs?.color) ? node.attrs.color : null
    const content = helpers.renderChildren(node)
    return color ? `<span data-jrc-text-color="${color}" style="color: ${color}">${content}</span>` : content
  },
  markdownTokenizer: {
    name: 'jrcTextColor',
    level: 'inline',
    start: source => source.indexOf('<span data-jrc-text-color='),
    tokenize(source, _tokens, lexer) {
      const match = /^<span data-jrc-text-color="(#[0-9a-f]{6})" style="color: \1">([\s\S]*?)<\/span>/.exec(source)
      if (!match || !TEXT_COLORS.includes(match[1])) return undefined
      return {
        type: 'jrcTextColor',
        raw: match[0],
        color: match[1],
        tokens: lexer.inlineTokens(match[2]),
      }
    },
  },
  markdownTokenName: 'jrcTextColor',
  parseMarkdown(token, helpers) {
    return helpers.applyMark('textColor', helpers.parseInline(token.tokens || []), { color: token.color })
  },
})

export function editorExtensions() {
  return [
    StarterKit.configure({ heading: { levels: [1, 2, 3] }, link: false }),
    TextColor,
    TaskList,
    TaskItem.configure({ nested: true }),
  ]
}
