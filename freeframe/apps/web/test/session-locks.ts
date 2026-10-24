import { vi } from 'vitest'

// Shared by independently imported tab modules. Queue by name like Web Locks;
// a per-module promise would incorrectly allow the tabs to overlap.
export function mockSessionLocks() {
  const queues = new Map<string, Promise<unknown>>()
  const request = vi.fn(<T>(name: string, callback: () => T | Promise<T>): Promise<T> => {
    const previous = queues.get(name) ?? Promise.resolve()
    const pending = previous.then(callback)
    queues.set(name, pending.catch(() => {}))
    return pending
  })
  vi.stubGlobal('navigator', { locks: { request } })
  return request
}
