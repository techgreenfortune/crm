import IndicatorIcon from '@/components/Icons/IndicatorIcon.vue'
import { parseColor, isTranslatable } from '@/utils'
import { defineStore } from 'pinia'
import { useTelemetry } from 'frappe-ui/frappe'
import { createListResource } from 'frappe-ui'
import { reactive, h } from 'vue'

export const statusesStore = defineStore('crm-statuses', () => {
  let leadStatusesByName = reactive({})
  let leadEngagementStatusesByName = reactive({})
  let dealStatusesByName = reactive({})
  let communicationStatusesByName = reactive({})

  const { capture } = useTelemetry()

  const leadStatuses = createListResource({
    doctype: 'CRM Lead Status',
    fields: ['name', 'color', 'position', 'type', 'stage_label'],
    orderBy: 'position asc',
    cache: 'lead-statuses',
    initialData: [],
    auto: true,
    transform(statuses) {
      for (let status of statuses) {
        status.color = parseColor(status.color)
        leadStatusesByName[status.name] = status
      }
      return statuses
    },
  })

  const leadEngagementStatuses = createListResource({
    doctype: 'CRM Lead Engagement Status',
    fields: ['name', 'color', 'position'],
    orderBy: 'position asc',
    cache: 'lead-engagement-statuses',
    initialData: [],
    auto: true,
    transform(statuses) {
      for (let status of statuses) {
        status.color = parseColor(status.color)
        leadEngagementStatusesByName[status.name] = status
      }
      return statuses
    },
  })

  const dealStatuses = createListResource({
    doctype: 'CRM Deal Status',
    fields: ['name', 'color', 'position', 'type'],
    orderBy: 'position asc',
    cache: 'deal-statuses',
    initialData: [],
    auto: true,
    transform(statuses) {
      for (let status of statuses) {
        status.color = parseColor(status.color)
        dealStatusesByName[status.name] = status
      }
      return statuses
    },
  })

  const communicationStatuses = createListResource({
    doctype: 'CRM Communication Status',
    fields: ['name'],
    cache: 'communication-statuses',
    initialData: [],
    auto: true,
    transform(statuses) {
      for (let status of statuses) {
        communicationStatusesByName[status.name] = status
      }
      return statuses
    },
  })

  function getLeadStatus(name) {
    if (!name) {
      name = leadStatuses.data[0].name
    }
    return leadStatusesByName[name]
  }

  function getLeadEngagementStatus(name) {
    if (!name) {
      name = leadEngagementStatuses.data?.[0]?.name
    }
    return leadEngagementStatusesByName[name]
  }

  function engagementStatusOptions(triggerLeadStatusChange = null) {
    const translatable = isTranslatable('CRM Lead Engagement Status')
    const options = []
    for (const status in leadEngagementStatusesByName) {
      const entry = leadEngagementStatusesByName[status]
      options.push({
        label: translatable ? __(entry?.name) : entry?.name,
        value: entry?.name,
        icon: () => h(IndicatorIcon, { class: entry?.color }),
        onClick: async () => {
          await triggerLeadStatusChange?.(entry?.name)
          capture('lead_status_changed', { doctype: 'lead', status })
        },
      })
    }
    return options
  }

  function getDealStatus(name) {
    if (!name) {
      name = dealStatuses.data[0].name
    }
    return dealStatusesByName[name]
  }

  function getCommunicationStatus(name) {
    if (!name) {
      name = communicationStatuses.data[0].name
    }
    return communicationStatuses[name]
  }

  function statusOptions(doctype, statuses = [], triggerStatusChange = null) {
    let statusesByName =
      doctype == 'deal' ? dealStatusesByName : leadStatusesByName

    if (statuses?.length) {
      statusesByName = statuses.reduce((acc, status) => {
        acc[status] = statusesByName[status]
        return acc
      }, {})
    }

    let translatable = isTranslatable(
      doctype == 'deal' ? 'CRM Deal Status' : 'CRM Lead Status',
    )

    let options = []
    for (const status in statusesByName) {
      options.push({
        label: translatable
          ? __(
              statusesByName[status]?.stage_label ||
                statusesByName[status]?.name,
            )
          : statusesByName[status]?.stage_label || statusesByName[status]?.name,
        value: statusesByName[status]?.name,
        icon: () => h(IndicatorIcon, { class: statusesByName[status]?.color }),
        onClick: async () => {
          await triggerStatusChange?.(statusesByName[status]?.name)
          capture('status_changed', { doctype, status })
        },
      })
    }
    return options
  }

  return {
    leadStatuses,
    leadEngagementStatuses,
    dealStatuses,
    communicationStatuses,
    getLeadStatus,
    getLeadEngagementStatus,
    getDealStatus,
    getCommunicationStatus,
    statusOptions,
    engagementStatusOptions,
  }
})
