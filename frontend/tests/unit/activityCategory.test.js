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
  it('classifies by actor and automation markers', () => {
    const cases = [
      [{ activity_type: 'changed', owner: human }, 'user'],
      [{ activity_type: 'changed', owner: 'Administrator' }, 'system'],
      [{ activity_type: 'creation', owner: 'Guest' }, 'system'],
      [{ activity_type: 'assignment_log', owner: 'Administrator' }, 'system'],
      [{ activity_type: 'comment', owner: human, content: 'hi' }, 'user'],
      [
        {
          activity_type: 'comment',
          owner: human,
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
      [{ activity_type: 'incoming_call', owner: 'Guest' }, 'user'],
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
        'user',
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
  const list = [
    { activity_type: 'changed', owner: human },
    { activity_type: 'changed', owner: 'Administrator' },
  ]

  it('returns everything for All', () => {
    expect(filterActivities(list, 'All')).toHaveLength(2)
  })

  it('splits User and System', () => {
    expect(filterActivities(list, 'User')).toEqual([list[0]])
    expect(filterActivities(list, 'System')).toEqual([list[1]])
  })
})
