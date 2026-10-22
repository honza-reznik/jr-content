'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { api } from '@/lib/api'
import { SetupWizard } from '@/components/auth/setup-wizard'
import type { SetupStatus } from '@/types'

export default function SetupPage() {
  const router = useRouter()
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    async function checkSetup() {
      try {
        const status = await api.get<SetupStatus>('/setup/status')
        if (!status.needs_setup) {
          router.replace('/login')
          return
        }
      } catch {
        // If the endpoint fails, allow the page to render (might be first run)
      } finally {
        setChecking(false)
      }
    }
    checkSetup()
  }, [router])

  if (checking) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-border border-t-accent" />
      </div>
    )
  }

  return <SetupWizard />
}
