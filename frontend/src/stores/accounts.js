import { defineStore } from 'pinia'
import { createResource } from 'frappe-ui'
import { reactive } from 'vue'
import { useRouter } from 'vue-router'

export const accountsStore = defineStore('crm-accounts', () => {
  let accountsByName = reactive({})

  const router = useRouter()

  const accounts = createResource({
    url: 'crm.api.session.get_accounts',
    cache: 'accounts',
    initialData: [],
    auto: true,
    transform(accounts) {
      for (let account of accounts) {
        accountsByName[account.name] = account
      }
      return accounts
    },
    onError(error) {
      if (error && error.exc_type === 'AuthenticationError') {
        router.push('/login')
      }
    },
  })

  function getAccount(name) {
    return accountsByName[name]
  }

  return {
    accounts,
    getAccount,
  }
})
