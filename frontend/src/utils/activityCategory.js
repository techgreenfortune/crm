// Mirrors crm/utils/user_activity.py — keep the two in sync.
const SYSTEM_USERS = ['Administrator', 'Guest']
const AUTOMATION_PREFIX = '[AUTOMATION]'
// Quote comments carry the automation prefix but record a person's action.
const USER_TRIGGERED_AUTOMATION_PREFIX = '[AUTOMATION] Quote'

export const ACTIVITY_FILTERS = ['All', 'User', 'System']

export function isSystemUser(user) {
  return !user || SYSTEM_USERS.includes(user)
}

export function isAutomatedComment(content) {
  const text = (content || '').replace(/^\s*(?:<[^>]+>\s*)*/, '')
  return (
    text.startsWith(AUTOMATION_PREFIX) &&
    !text.startsWith(USER_TRIGGERED_AUTOMATION_PREFIX)
  )
}

export function activityCategory(activity) {
  switch (activity.activity_type) {
    case 'incoming_call':
    case 'outgoing_call':
    case 'note':
      return 'user'
    case 'communication':
      return activity.communication_type === 'Automated Message'
        ? 'system'
        : 'user'
    case 'comment':
      return isSystemUser(activity.owner) ||
        isAutomatedComment(activity.content)
        ? 'system'
        : 'user'
    default:
      return isSystemUser(activity.owner) ? 'system' : 'user'
  }
}

export function filterActivities(activities, filter) {
  if (!filter || filter === 'All') return activities
  const wanted = filter.toLowerCase()
  return activities.filter((a) => activityCategory(a) === wanted)
}
