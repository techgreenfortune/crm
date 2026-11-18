import { describe, it, expect } from 'vitest'
import {
  activityCategory,
  filterActivities,
  isAutomatedComment,
} from '@/utils/activityCategory'

const human = 'se@example.com'

describe('isAutomatedComment', () => {
  it('flags automation comments, including HTML-wrapped ones', () => {
    expect(isAutomatedComment('[AUTOMATION] Lead moved to Cold')).toBe(true)
    expect(isAutomatedComment('<p>[AUTOMATION] Brevo enrollment')).toBe(true)
  })

  it('treats quote automation comments and normal text as user', () => {
    expect(isAutomatedComment('[AUTOMATION] Quote Accepted.')).toBe(false)
    expect(isAutomatedComment('<p>Customer wants a revisit</p>')).toBe(false)
    expect(isAutomatedComment(null)).toBe(false)
  })
})

describe('activityCategory', () => {
  it('classifies sales work as user, automation as system, rest as other', () => {
    const cases = [
      [
        { activity_type: 'changed', owner: human, is_status_change: true },
        'user',
      ],
      [
        {
          activity_type: 'changed',
          owner: 'Administrator',
          is_status_change: true,
        },
        'user',
      ],
      [{ activity_type: 'changed', owner: human }, 'other'],
      [{ activity_type: 'added', owner: human }, 'other'],
      [{ activity_type: 'creation', owner: human }, 'other'],
      [{ activity_type: 'creation', owner: 'Guest' }, 'system'],
      [{ activity_type: 'comment', owner: human, content: 'hi' }, 'user'],
      [
        {
          activity_type: 'comment',
          owner: 'Administrator',
          content: '[AUTOMATION] Lead reactivated',
        },
        'system',
      ],
      [
        {
          activity_type: 'comment',
          owner: human,
          content: '[AUTOMATION] Quote uploaded — Value: 10',
        },
        'user',
      ],
      [
        { activity_type: 'incoming_call', owner: 'Guest', status: 'Completed' },
        'user',
      ],
      [
        {
          activity_type: 'incoming_call',
          owner: 'Guest',
          status: 'Call Not Answered',
        },
        'other',
      ],
      [{ activity_type: 'outgoing_call', status: 'Busy' }, 'user'],
      [{ activity_type: 'note', owner: human }, 'user'],
      [
        {
          activity_type: 'communication',
          communication_type: 'Automated Message',
        },
        'system',
      ],
      [
        { activity_type: 'communication', communication_type: 'Communication' },
        'other',
      ],
    ]
    for (const [activity, expected] of cases) {
      expect(activityCategory(activity), JSON.stringify(activity)).toBe(
        expected,
      )
    }
  })
})

describe('filterActivities', () => {
  const stage = {
    activity_type: 'changed',
    owner: human,
    is_status_change: true,
  }
  const edit = { activity_type: 'changed', owner: human }
  const automation = {
    activity_type: 'comment',
    owner: 'Administrator',
    content: '[AUTOMATION] Lead reactivated',
  }
  const list = [stage, edit, automation]

  it('returns everything for All', () => {
    expect(filterActivities(list, 'All')).toBe(list)
  })

  it('keeps only sales work for User and automation for System', () => {
    expect(filterActivities(list, 'User')).toEqual([stage])
    expect(filterActivities(list, 'System')).toEqual([automation])
  })

  it('pulls a stage change out of a grouped version', () => {
    const group = { ...edit, other_versions: [stage] }
    expect(filterActivities([group], 'User')).toEqual([stage])
  })
})
