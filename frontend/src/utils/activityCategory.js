// Same split as crm/utils/user_activity.py — keep the two in sync.
// User: calls, C-stage changes, comments, notes, quote events.
// System: automation. Everything else (data corrections, reassignment,
// emails, lead creation) is neither and only shows under All.
const SYSTEM_USERS = ['Guest']
const AUTOMATION_PREFIX = '[AUTOMATION]'
// Quote comments carry the automation prefix but record a person's action.
const USER_TRIGGERED_AUTOMATION_PREFIX = '[AUTOMATION] Quote'
const CALL_TYPES = ['incoming_call', 'outgoing_call']

export const ACTIVITY_FILTERS = ['All', 'User', 'System']

export function isSystemUser(user) {
  return SYSTEM_USERS.includes(user)
}

export function isAutomatedComment(content) {
  const text = (content || '').replace(/^\s*(?:<[^>]+>\s*)*/, '')
  return (
    text.startsWith(AUTOMATION_PREFIX) &&
    !text.startsWith(USER_TRIGGERED_AUTOMATION_PREFIX)
  )
}

export function activityCategory(activity) {
  const type = activity.activity_type
  // Telephony webhooks save calls as Guest, but every call is a person's action.
  if (CALL_TYPES.includes(type)) return 'user'
  if (type === 'communication') {
    return activity.communication_type === 'Automated Message'
      ? 'system'
      : 'other'
  }
  if (isSystemUser(activity.owner)) return 'system'
  if (type === 'comment') {
    return isAutomatedComment(activity.content) ? 'system' : 'user'
  }
  if (type === 'note') return 'user'
  if (activity.is_status_change) return 'user'
  return 'other'
}

// Grouped versions hide all but the first change; split them so a stage
// change inside a group is filtered on its own.
function expandGroups(activities) {
  return activities.flatMap((a) =>
    a.other_versions
      ? [{ ...a, other_versions: undefined }, ...a.other_versions]
      : [a],
  )
}

export function filterActivities(activities, filter) {
  if (!filter || filter === 'All') return activities
  const wanted = filter.toLowerCase()
  return expandGroups(activities).filter(
    (a) => activityCategory(a) === wanted,
  )
}
