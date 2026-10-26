'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { removeTokens } from '@/lib/auth'
import { clearSsoBinding } from '@/lib/jrc-sso'
import { consumeLogoutGrant } from '@/lib/jrc-logout'
import { Button } from '@/components/ui/button'

async function signOut() {
  await removeTokens()
  clearSsoBinding()
  document.cookie = `ff_setup_done=; path=${process.env.NEXT_PUBLIC_BASE_PATH || '/'}; max-age=0`
  window.location.replace(`${(process.env.NEXT_PUBLIC_BASE_PATH || '').replace(/\/video$/, '')}/accounts/login/`)
}

export default function JrcLogoutPage() {
  const started = useRef(false)
  const [confirm, setConfirm] = useState(false)
  const [failed, setFailed] = useState(false)
  const finishSignOut = useCallback(async () => {
    try { await signOut() } catch { setFailed(true) }
  }, [])
  useEffect(() => {
    if (started.current) return
    started.current = true
    consumeLogoutGrant().then(authorized => {
      if (authorized) return finishSignOut()
      else setConfirm(true)
    }).catch(() => setConfirm(true))
  }, [finishSignOut])

  if (failed) return <p role="alert">Video sign-out could not be completed. Please try again in an up-to-date browser.</p>

  return confirm ? (
    <div>
      <h1 className="text-xl font-semibold mb-3">Sign out of video?</h1>
      <p className="text-text-secondary mb-4">Confirm to end your video session in this browser.</p>
      <Button onClick={finishSignOut}>Sign out of video</Button>
      <a className="ml-4 text-accent underline" href={`${(process.env.NEXT_PUBLIC_BASE_PATH || "").replace(/\/video$/, "")}/`}>Cancel</a>
    </div>
  ) : <p role="status">Signing out of video…</p>
}
