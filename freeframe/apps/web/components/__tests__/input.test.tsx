import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Input } from '../ui/input'

describe('Input component', () => {
  it('renders with a label', () => {
    render(<Input label="Email address" />)
    expect(screen.getByLabelText('Email address')).toBeInTheDocument()
    expect(screen.getByText('Email address')).toBeInTheDocument()
  })

  it('renders without label', () => {
    render(<Input placeholder="Enter value" />)
    expect(screen.getByPlaceholderText('Enter value')).toBeInTheDocument()
  })

  it('shows error message when error prop is provided', () => {
    render(<Input label="Email" error="Invalid email address" />)
    expect(screen.getByText('Invalid email address')).toBeInTheDocument()
  })

  it('applies error styling when error prop is present', () => {
    render(<Input error="Required" />)
    const input = screen.getByRole('textbox')
    expect(input.className).toContain('border-status-error')
  })

  it('handles onChange events', () => {
    const handleChange = vi.fn()
    render(<Input onChange={handleChange} />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'test' } })
    expect(handleChange).toHaveBeenCalledOnce()
  })

  it('forwards additional props to input element', () => {
    render(<Input placeholder="Type here" type="email" />)
    const input = screen.getByPlaceholderText('Type here')
    expect(input).toHaveAttribute('type', 'email')
  })

  it('renders with custom className', () => {
    render(<Input className="my-custom-class" />)
    const input = screen.getByRole('textbox')
    expect(input.className).toContain('my-custom-class')
  })
})
