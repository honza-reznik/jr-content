import * as React from 'react'
import { cn } from '@/lib/utils'

// ─── Base Skeleton ────────────────────────────────────────────────────────────

interface SkeletonProps extends React.HTMLAttributes<HTMLDivElement> {
  className?: string
}

export function Skeleton({ className, ...props }: SkeletonProps) {
  return (
    <div
      className={cn(
        'animate-pulse rounded-md bg-bg-hover',
        className,
      )}
      {...props}
    />
  )
}

// ─── SkeletonText (single line) ───────────────────────────────────────────────

interface SkeletonTextProps {
  className?: string
  /** Width as a Tailwind class, e.g. "w-1/2", "w-32". Defaults to "w-full" */
  width?: string
}

export function SkeletonText({ className, width = 'w-full' }: SkeletonTextProps) {
  return <Skeleton className={cn('h-4', width, className)} />
}

// ─── SkeletonAvatar (circle) ──────────────────────────────────────────────────

interface SkeletonAvatarProps {
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const avatarSizes: Record<NonNullable<SkeletonAvatarProps['size']>, string> = {
  sm: 'h-7 w-7',
  md: 'h-9 w-9',
  lg: 'h-12 w-12',
}

export function SkeletonAvatar({ size = 'md', className }: SkeletonAvatarProps) {
  return (
    <Skeleton
      className={cn('shrink-0 rounded-full', avatarSizes[size], className)}
    />
  )
}

// ─── SkeletonCard (thumbnail + 2 lines of text) ───────────────────────────────

interface SkeletonCardProps {
  className?: string
}

export function SkeletonCard({ className }: SkeletonCardProps) {
  return (
    <div
      className={cn(
        'flex flex-col gap-3 rounded-xl border border-border bg-bg-secondary p-3',
        className,
      )}
    >
      {/* Thumbnail */}
      <Skeleton className="aspect-video w-full rounded-lg" />

      {/* Text lines */}
      <div className="space-y-2 px-0.5">
        <SkeletonText width="w-3/4" />
        <SkeletonText width="w-1/2" />
      </div>
    </div>
  )
}

// ─── SkeletonList (rows of varying width) ─────────────────────────────────────

interface SkeletonListProps {
  rows?: number
  className?: string
}

const ROW_WIDTHS = ['w-full', 'w-4/5', 'w-3/4', 'w-5/6', 'w-2/3']

export function SkeletonList({ rows = 5, className }: SkeletonListProps) {
  return (
    <div className={cn('space-y-3', className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3">
          <SkeletonAvatar size="sm" />
          <div className="flex-1 space-y-2">
            <SkeletonText width={ROW_WIDTHS[i % ROW_WIDTHS.length]} />
            <SkeletonText width="w-1/3" />
          </div>
        </div>
      ))}
    </div>
  )
}

// ─── SkeletonGrid (grid of cards) ─────────────────────────────────────────────

interface SkeletonGridProps {
  count?: number
  className?: string
}

export function SkeletonGrid({ count = 6, className }: SkeletonGridProps) {
  return (
    <div
      className={cn(
        'grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3',
        className,
      )}
    >
      {Array.from({ length: count }).map((_, i) => (
        <SkeletonCard key={i} />
      ))}
    </div>
  )
}
