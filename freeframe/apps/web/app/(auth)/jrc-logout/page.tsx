'use client'

import { useEffect, useRef, useState } from 'react'
import { removeTokens } from '@/lib/auth'
import { clearSsoBinding } from '@/lib/jrc-sso'
import { consumeLogoutGrant } from '@/lib/jrc-logout'
import { Button } from '@/components/ui/button'

function signOut() {
  removeTokens()
  clearSsoBinding()
  document.cookie = 'ff_setup_done=; path=/; max-age=0'
  window.location.replace('/accounts/login/')
}

export default function JrcLogoutPage() {
  const started = useRef(false)
  const [confirm, setConfirm] = useState(false)
  useEffect(() => {
    if (started.current) return
    started.current = true
    consumeLogoutGrant().then(authorized => {
      if (authorized) signOut()
      else setConfirm(true)
    }).catch(() => setConfirm(true))
  }, [])

  return confirm ? (
    <div>
      <h1 className="text-xl font-semibold mb-3">Sign out of video?</h1>
      <p className="text-text-secondary mb-4">Confirm to end your video session in this browser.</p>
      <Button onClick={signOut}>Sign out of video</Button>
      <a className="ml-4 text-accent underline" href="/">Cancel</a>
    </div>
  ) : <p role="status">Signing out of video…</p>
}
