import { describe, it, expect } from 'vitest'
import { getFriendlyDocError } from '@/utils/friendlyError'

describe('getFriendlyDocError', () => {
  const perm = {
    exc_type: 'PermissionError',
    messages: ['Not allowed via controller permission check'],
  }

  it('lead PermissionError mentions reassignment, hides server text', () => {
    const r = getFriendlyDocError(perm, 'CRM Lead')
    expect(r.title).toBe("You don't have access")
    expect(r.message).toMatch(/reassigned or moved to another stage/)
    expect(r.message).not.toMatch(/controller/)
  })

  it('non-lead PermissionError has no stage wording', () => {
    const r = getFriendlyDocError(perm, 'Contact')
    expect(r.message).toBe("You don't have access to this record.")
  })

  it('maps DoesNotExistError', () => {
    expect(
      getFriendlyDocError({ exc_type: 'DoesNotExistError' }, 'CRM Lead').title,
    ).toBe('Lead not found')
    expect(
      getFriendlyDocError({ exc_type: 'DoesNotExistError' }, 'Contact').title,
    ).toBe('Record not found')
  })

  it('hides technical fallback messages', () => {
    const r = getFriendlyDocError(
      { exc_type: 'ValidationError', messages: ['Error in doctype CRM Lead'] },
      'CRM Lead',
    )
    expect(r.title).toBe('Something went wrong')
    expect(r.message).toMatch(/refresh and try again/)
  })

  it('keeps plain user-facing messages', () => {
    const r = getFriendlyDocError(
      { exc_type: 'ValidationError', messages: ['Phone is required'] },
      'CRM Lead',
    )
    expect(r.message).toBe('Phone is required')
  })
})
