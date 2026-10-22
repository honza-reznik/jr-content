import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { EmptyState } from '../shared/empty-state'
import { FolderOpen } from 'lucide-react'

describe('EmptyState component', () => {
  it('renders with title', () => {
    render(<EmptyState title="No assets found" />)
    expect(screen.getByText('No assets found')).toBeInTheDocument()
  })

  it('renders with title and description', () => {
    render(
      <EmptyState
        title="No projects yet"
        description="Create your first project to get started"
      />,
    )
    expect(screen.getByText('No projects yet')).toBeInTheDocument()
    expect(screen.getByText('Create your first project to get started')).toBeInTheDocument()
  })

  it('does not render description when not provided', () => {
    render(<EmptyState title="Empty" />)
    expect(screen.queryByText(/description/i)).not.toBeInTheDocument()
  })

  it('renders action button when action is provided', () => {
    const handleClick = vi.fn()
    render(
      <EmptyState
        title="No items"
        action={{ label: 'Create Item', onClick: handleClick }}
      />,
    )
    const button = screen.getByRole('button', { name: 'Create Item' })
    expect(button).toBeInTheDocument()
  })

  it('action button click fires handler', () => {
    const handleClick = vi.fn()
    render(
      <EmptyState
        title="No items"
        action={{ label: 'Add New', onClick: handleClick }}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Add New' }))
    expect(handleClick).toHaveBeenCalledOnce()
  })

  it('does not render action button when no action provided', () => {
    render(<EmptyState title="Empty" />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('renders with icon', () => {
    render(<EmptyState title="No folders" icon={FolderOpen} />)
    // SVG icon should be present in the DOM
    const container = screen.getByText('No folders').closest('div')!.parentElement
    expect(container?.querySelector('svg')).toBeInTheDocument()
  })

  it('applies custom className', () => {
    const { container } = render(<EmptyState title="Test" className="my-custom" />)
    expect(container.firstChild).toHaveClass('my-custom')
  })
})
