// Serverový převod používá stejné schéma jako prototyp F1-B03, bez prohlížeče a sítě.
import { readFileSync } from 'node:fs'
import { validateContent, markdownToContent, contentToMarkdown } from './content.mjs'

try {
  const input = JSON.parse(readFileSync(0, 'utf8'))
  const content = input.markdown !== undefined
    ? markdownToContent(input.markdown)
    : validateContent(input.content)
  process.stdout.write(JSON.stringify({ content, text: contentToMarkdown(content) }))
} catch {
  process.stdout.write(JSON.stringify({ error: 'Invalid editor content or Markdown.' }))
  process.exitCode = 1
}
