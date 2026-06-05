<template>
  <div></div>
</template>
<script setup>
import { inject } from 'vue'
import { useDoctypeModal } from '@/composables/doctypeModal'
import { useOnboarding, useTelemetry } from 'frappe-ui/frappe'
import { call, toast } from 'frappe-ui'
import { useRoute, useRouter } from 'vue-router'

// Provided by Lead.vue (and any other parent that wants its resources refreshed
// when a child modal saves). null fallback for parents that don't provide it.
const reloadAfterChildModal = inject('reloadAfterChildModal', null)

const props = defineProps({
  doctype: { type: String, default: '' },
  doc: { type: Object, default: () => ({}) },
})

const activities = defineModel({ type: Object })

const { showModal } = useDoctypeModal()
const { updateOnboardingStep } = useOnboarding('frappecrm')
const { capture } = useTelemetry()

async function requestQuote(leadName) {
  if (!leadName) return
  if (
    !window.confirm(
      __('Create a new Quote Request and Upload Quote task for this lead?'),
    )
  ) {
    return
  }
  try {
    const qrName = await call('crm.api.quotes.request_quote', {
      lead: leadName,
    })
    activities.value.reload()
    reloadAfterChildModal?.()
    toast.success(__('Quote Request {0} ready', [qrName]))
  } catch (err) {
    toast.error(err?.message || __('Could not request a quote.'))
  }
}

async function showQuoteRequest(
  leadName,
  title = 'Quote Request',
  qrName = null,
) {
  let resolvedName = qrName
  if (resolvedName) {
    // Verify QR belongs to this lead — guards stale FK from corrupted task data
    const verified = await call('frappe.client.get_value', {
      doctype: 'CRM Quote Request',
      filters: { name: resolvedName, lead: leadName },
      fieldname: 'name',
    })
    if (!verified) {
      toast.error(__('This task is not linked to a valid Quote Request.'))
      return
    }
  } else {
    const rows = await call('frappe.client.get_list', {
      doctype: 'CRM Quote Request',
      filters: { lead: leadName, is_superseded: 0 },
      fields: ['name'],
      order_by: 'creation desc',
      limit: 1,
    })
    if (!rows?.length) return
    resolvedName = rows[0].name
  }
  showModal({
    name: resolvedName,
    doctype: 'CRM Quote Request',
    customTitle: title,
    callbacks: {
      afterUpdate: () => {
        activities.value.reload()
        reloadAfterChildModal?.()
      },
    },
  })
}

// Tasks
function showTask(task) {
  showModal({
    name: task?.name,
    doctype: 'CRM Task',
    title: 'Task',
    defaults: {
      reference_doctype: props.doctype,
      reference_docname: props.doc?.name,
    },
    callbacks: {
      afterInsert: (d) => afterDoctype(d, true),
      afterUpdate: afterDoctype,
    },
  })
}

function deleteTask(name) {
  call('frappe.client.delete', {
    doctype: 'CRM Task',
    name,
  })
    .then(() => {
      activities.value.reload()
      reloadAfterChildModal?.()
    })
    .catch((err) => {
      activities.value.reload()
      reloadAfterChildModal?.()
      toast.error(
        err?.message || __('You are not permitted to delete this task.'),
      )
    })
}

function updateTaskStatus(status, task) {
  call('frappe.client.set_value', {
    doctype: 'CRM Task',
    name: task.name,
    fieldname: 'status',
    value: status,
  })
    .then(() => {
      activities.value.reload()
      reloadAfterChildModal?.()
    })
    .catch((err) => {
      activities.value.reload()
      reloadAfterChildModal?.()
      toast.error(
        err?.message || __('You are not permitted to update this task.'),
      )
    })
}

// Notes
function showNote(note) {
  showModal({
    name: note?.name,
    doctype: 'FCRM Note',
    title: 'Note',
    defaults: {
      reference_doctype: props.doctype,
      reference_docname: props.doc?.name,
    },
    callbacks: {
      afterInsert: (d) => afterDoctype(d, true),
      afterUpdate: afterDoctype,
    },
  })
}

function afterDoctype(d, isInsert = false) {
  activities.value.reload()
  reloadAfterChildModal?.()

  let name =
    d.doctype == 'FCRM Note'
      ? 'note'
      : d.doctype == 'CRM Task'
        ? 'task'
        : 'call_log'

  let redirectHash = name + 's'
  if (d.doctype == 'CRM Call Log') {
    redirectHash = 'calls'
  }

  if (isInsert) {
    updateOnboardingStep('create_first_' + name)
    capture(name + '_created')
  } else {
    capture(name + '_updated')
  }

  redirect(redirectHash)
}

// Call Logs
function createCallLog() {
  showModal({
    doctype: 'CRM Call Log',
    title: 'Call Log',
    defaults: {
      reference_doctype: props.doctype,
      reference_docname: props.doc?.name,
      reference_doc: { ...props.doc },
    },
    callbacks: {
      afterInsert: (d) => afterDoctype(d, true),
      afterUpdate: afterDoctype,
    },
  })
}

// common
const route = useRoute()
const router = useRouter()

function redirect(tabName) {
  if (route.name == 'Lead' || route.name == 'Deal') {
    let hash = '#' + tabName
    if (route.hash != hash) {
      router.push({ ...route, hash })
    }
  }
}

defineExpose({
  showTask,
  deleteTask,
  updateTaskStatus,
  showNote,
  createCallLog,
  showQuoteRequest,
  requestQuote,
})
</script>
