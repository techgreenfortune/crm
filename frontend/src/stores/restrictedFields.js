import { defineStore } from 'pinia'
import { createResource } from 'frappe-ui'

export const restrictedFieldsStore = defineStore(
  'crm-restricted-fields',
  () => {
    const hiddenFields = createResource({
      url: 'crm.permissions.field_visibility.get_my_hidden_fields',
      initialData: {},
      auto: true,
    })

    function getHiddenFields(doctype) {
      return hiddenFields.data?.[doctype] || []
    }

    // Callers that act on the result (mandatory check) must not read the empty
    // initialData before the first fetch resolves.
    async function ensureLoaded() {
      if (hiddenFields.fetched) return
      try {
        await (hiddenFields.promise || hiddenFields.fetch())
      } catch {
        // Fall back to "nothing hidden"; the server still enforces.
      }
    }

    return { hiddenFields, getHiddenFields, ensureLoaded }
  },
)
