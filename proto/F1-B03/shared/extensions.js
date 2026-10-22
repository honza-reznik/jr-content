// Společná sada rozšíření editoru: stejná v prohlížeči (editor) i na serveru (kontrola dat, Markdown).
import StarterKit from '@tiptap/starter-kit'
import { TaskList, TaskItem } from '@tiptap/extension-list'

export function editorExtensions() {
  return [
    StarterKit.configure({ heading: { levels: [1, 2, 3] }, link: false }),
    TaskList,
    TaskItem.configure({ nested: true }),
  ]
}
