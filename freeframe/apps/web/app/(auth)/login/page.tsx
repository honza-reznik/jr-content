'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/api'
import { restoreSessionCookies } from '@/lib/auth'
import { safeAppDestination } from '@/lib/app-destination'
import { LoginForm } from '@/components/auth/login-form'
import { PoweredByBadge } from '@/components/shared/powered-by-badge'
import type { SetupStatus } from '@/types'


export default function LoginPage() {
  const router = useRouter()

  useEffect(() => {
    async function checkSetup() {
      try {
        const status = await api.get<SetupStatus>('/setup/status')
        if (status.needs_setup) {
          router.replace('/setup')
        }
      } catch {
        // ignore
      }
    }

    async function restoreSession() {
      try {
        if (await restoreSessionCookies()) {
          const params = new URLSearchParams(window.location.search)
          router.replace(safeAppDestination(params.get('from')) || '/projects')
          return
        }
      } catch {
        // A browser without a session lock must not write cookies.
      }
      await checkSetup()
    }
    void restoreSession()
  }, [router])

  return (
    <>
      {/* The branded logo + org name live in the (auth) layout, so every auth screen gets them */}
      <LoginForm />

      {/* Powered by FreeFrame */}
      <PoweredByBadge className="mt-6 text-center justify-center w-full" />
    </>
  )
}
