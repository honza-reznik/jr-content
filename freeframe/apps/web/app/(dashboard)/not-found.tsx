import * as React from 'react'
import Link from 'next/link'
import { FileQuestion, ArrowLeft } from 'lucide-react'

export default function DashboardNotFound() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 px-4 py-16 text-center">
      {/* Illustration */}
      <div className="relative flex h-24 w-24 items-center justify-center rounded-2xl border border-border bg-bg-secondary">
        <FileQuestion className="h-12 w-12 text-text-tertiary" />
        <span className="absolute -right-2 -top-2 flex h-7 w-7 items-center justify-center rounded-full border border-border bg-bg-elevated text-xs font-bold text-text-secondary">
          404
        </span>
      </div>

      {/* Copy */}
      <div className="space-y-2">
        <h2 className="text-xl font-semibold text-text-primary">
          Page not found
        </h2>
        <p className="max-w-xs text-sm text-text-secondary">
          The page you&apos;re looking for doesn&apos;t exist or has been moved.
        </p>
      </div>

      {/* Back to home */}
      <Link
        href="/"
        className="flex items-center gap-2 rounded-lg border border-border bg-bg-secondary px-4 py-2 text-sm font-medium text-text-primary transition-colors hover:bg-bg-hover"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to home
      </Link>
    </div>
  )
}
