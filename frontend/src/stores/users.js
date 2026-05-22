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

  // Role metadata is authoritative at crm/permissions/role_config.py. We fetch
  // it once per app session and derive isManager / isSalesUser / hierarchy
  // rank from it. Until this resolves, isManager() returns false — same as
  // before (which gated on getUser(email).role, also fetch-bound).
  const roleConfig = createResource({
    url: 'crm.permissions.role_config.get_hierarchy_role_config',
    cache: 'hierarchy-role-config',
    auto: true,
  })

  const tier1FullRw = computed(
    () => new Set(roleConfig.data?.tier1_full_rw || []),
  )
  const roleRank = computed(() => roleConfig.data?.role_rank || {})

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

  // Tier-1 full-RW per the access matrix. Sourced from
  // crm/permissions/role_config.py:TIER1_FULL_RW via roleConfig fetch.
  function isManager(email) {
    const role = getUser(email).role
    return !!role && tier1FullRw.value.has(role)
  }

  function isWebsiteUser(email) {
    return getUser(email).user_type === 'Website User'
  }

  // Any non-tier-1 role that is in the CRM role matrix. The `role in
  // roleRank.value` membership check prevents stray Frappe defaults
  // (e.g. raw "Sales Manager" / "Sales User") from being classified as
  // sales users — only the 13 matrix roles minus tier-1 qualify.
  function isSalesUser(email) {
    const role = getUser(email).role
    return !!role && role in roleRank.value && !tier1FullRw.value.has(role)
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
    roleConfig,
    roleRank,
    tier1FullRw,
  }
})
