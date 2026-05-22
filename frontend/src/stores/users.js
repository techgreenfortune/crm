import { defineStore } from 'pinia'
import { createResource } from 'frappe-ui'
import { sessionStore } from './session'
import { computed, reactive } from 'vue'
import { useRouter } from 'vue-router'

export const usersStore = defineStore('crm-users', () => {
  const session = sessionStore()

  let usersByName = reactive({})
  const router = useRouter()

  const users = createResource({
    url: 'crm.api.session.get_users',
    cache: 'crm-users',
    initialData: [],
    auto: true,
    transform([allUsers, crmUsers]) {
      for (let user of allUsers) {
        usersByName[user.name] = user
        if (user.name === 'Administrator') {
          usersByName[user.email] = user
        }
      }
      return { allUsers, crmUsers }
    },
    onError(error) {
      if (error && error.exc_type === 'AuthenticationError') {
        window.location.href = '/login?redirect-to=/crm'
        return
      }
      // Stale browser cookie with invalidated server session (e.g. after docker restart):
      // Frappe treats the request as Guest and reports the function as "not whitelisted".
      // Distinguish from genuine "no CRM role" PermissionError by checking the traceback.
      if (
        error &&
        error.exc_type === 'PermissionError' &&
        error.exc?.includes('whitelisted')
      ) {
        window.location.href = '/login?redirect-to=/crm'
      }
    },
  })

  function getUser(email) {
    if (!email || email === 'sessionUser') {
      email = session.user
    }
    if (!usersByName[email]) {
      usersByName[email] = {
        name: email,
        email: email,
        full_name: email.split('@')[0],
        first_name: email.split('@')[0],
        last_name: '',
        user_image: null,
        role: null,
      }
    }
    return usersByName[email]
  }

  function isAdmin(email) {
    return getUser(email).role === 'System Manager'
  }

  // Tier-1 full-RW roles per the access matrix. Mirrors the backend's
  // crm.overrides.crm_lead_permissions.TIER1_FULL_RW set.
  function isManager(email) {
    const role = getUser(email).role
    return role === 'Sales Head' || role === 'Sales Coordinator' || isAdmin(email)
  }

  function isWebsiteUser(email) {
    return getUser(email).user_type === 'Website User'
  }

  // Any non-managerial CRM role. Kept as a Set for fast lookup.
  const _SALES_USER_ROLES = new Set([
    'CRM User',
    'Sales Executive',
    'Project Sales Executive',
    'ASM',
    'RSM',
    'Marketing',
    'Calling Team',
    'Jr. Sales Executive',
    'B2F Team',
    'Estimation Team',
    'Management',
  ])

  function isSalesUser(email) {
    return _SALES_USER_ROLES.has(getUser(email).role)
  }

  function isTelephonyAgent(email) {
    return getUser(email).is_telphony_agent
  }

  function getUserRole(email) {
    const user = getUser(email)
    if (user && user.role) {
      return user.role
    }
    return null
  }

  const isCrmUser = (user) => {
    user = user || session.user
    return users.data.crmUsers?.find((u) => u.name === user)
  }

  return {
    users,
    allUsers: computed(() => users.data.allUsers),
    crmUsers: computed(() => users.data.crmUsers),
    getUser,
    isAdmin,
    isManager,
    isSalesUser,
    isTelephonyAgent,
    getUserRole,
    isWebsiteUser,
    isCrmUser,
  }
})
