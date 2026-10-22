import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SkeletonCard, SkeletonGrid, SkeletonList } from '../shared/skeleton'

describe('Skeleton components', () => {
  describe('SkeletonCard', () => {
    it('renders a skeleton card', () => {
      const { container } = render(<SkeletonCard />)
      expect(container.firstChild).toBeInTheDocument()
      // Should have the card wrapper
      expect(container.querySelector('[class*="rounded-xl"]')).toBeInTheDocument()
    })

    it('applies custom className', () => {
      const { container } = render(<SkeletonCard className="my-custom" />)
      expect(container.firstChild).toHaveClass('my-custom')
    })
  })

  describe('SkeletonGrid', () => {
    it('renders default 6 skeleton cards', () => {
      const { container } = render(<SkeletonGrid />)
      // Each card has an aspect-video skeleton inside
      const cards = container.querySelectorAll('[class*="aspect-video"]')
      expect(cards).toHaveLength(6)
    })

    it('renders N skeleton cards when count is specified', () => {
      const { container } = render(<SkeletonGrid count={3} />)
      const cards = container.querySelectorAll('[class*="aspect-video"]')
      expect(cards).toHaveLength(3)
    })

    it('renders 1 skeleton card', () => {
      const { container } = render(<SkeletonGrid count={1} />)
      const cards = container.querySelectorAll('[class*="aspect-video"]')
      expect(cards).toHaveLength(1)
    })
  })

  describe('SkeletonList', () => {
    it('renders default 5 skeleton rows', () => {
      const { container } = render(<SkeletonList />)
      // Each row has a flex items-center gap-3 wrapper
      const rows = container.querySelectorAll('[class*="flex items-center gap-3"]')
      expect(rows).toHaveLength(5)
    })

    it('renders N skeleton rows when rows is specified', () => {
      const { container } = render(<SkeletonList rows={3} />)
      const rows = container.querySelectorAll('[class*="flex items-center gap-3"]')
      expect(rows).toHaveLength(3)
    })
  })
})
