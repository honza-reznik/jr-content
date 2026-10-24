'use client'

import { useEffect, useRef, useState } from 'react'
import { exchangeBrowserTicket } from '@/lib/jrc-sso'

export default function JrcSsoPage() {
  const started = useRef(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    // React StrictMode replays effects; the single-use exchange must run once.
    if (started.current) return
    started.current = true
    exchangeBrowserTicket().then(destination => {
      window.location.replace(destination)
    }).catch(() => setFailed(true))
  }, [])

  return failed ? (
    <div role="alert">
      <h1 className="text-xl font-semibold mb-3">Video sign-in could not be completed</h1>
      <p className="text-text-secondary mb-4">Please return to JR Content and open video again.</p>
      <a className="text-accent underline" href="/">Back to JR Content</a>
    </div>
  ) : <p role="status">Opening video…</p>
}
