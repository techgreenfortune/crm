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

    return { hiddenFields, getHiddenFields }
  },
)
