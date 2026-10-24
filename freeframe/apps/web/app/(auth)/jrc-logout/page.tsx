'use client'

import { useEffect } from 'react'
import { removeTokens } from '@/lib/auth'
import { clearSsoBinding } from '@/lib/jrc-sso'

export default function JrcLogoutPage() {
  useEffect(() => {
    removeTokens()
    clearSsoBinding()
    document.cookie = 'ff_setup_done=; path=/; max-age=0'
    // Fixed return address: query/fragment parameters cannot change it.
    window.location.replace('/accounts/login/')
  }, [])

  return <p role="status">Signing out of video…</p>
}
