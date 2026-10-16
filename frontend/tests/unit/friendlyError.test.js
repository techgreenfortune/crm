import { describe, it, expect } from 'vitest'
import { getFriendlyDocError } from '@/utils/friendlyError'

describe('getFriendlyDocError', () => {
  it('maps PermissionError to friendly access message', () => {
    const r = getFriendlyDocError(
      {
        exc_type: 'PermissionError',
        messages: ['No permission for CRM Lead X'],
      },
      'lead',
    )
    expect(r.title).toBe('Access removed')
    expect(r.message).not.toMatch(/No permission/)
    expect(r.message).toMatch(/no longer have access to this lead/)
  })

  it('maps DoesNotExistError', () => {
    const r = getFriendlyDocError({ exc_type: 'DoesNotExistError' }, 'deal')
    expect(r.title).toBe('Deal not found')
  })

  it('hides technical fallback messages', () => {
    const r = getFriendlyDocError(
      { exc_type: 'ValidationError', messages: ['Error in doctype CRM Lead'] },
      'lead',
    )
    expect(r.title).toBe('Something went wrong')
    expect(r.message).toMatch(/refresh and try again/)
  })

  it('keeps plain user-facing messages', () => {
    const r = getFriendlyDocError(
      { exc_type: 'ValidationError', messages: ['Phone is required'] },
      'lead',
    )
    expect(r.message).toBe('Phone is required')
  })
})
