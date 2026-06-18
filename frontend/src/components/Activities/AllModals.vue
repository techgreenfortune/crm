<template>
  <div />
</template>
<script setup>
import { inject } from 'vue'
import { call, toast } from 'frappe-ui'
import { useRoute, useRouter } from 'vue-router'
import { useOnboarding, useTelemetry } from 'frappe-ui/frappe'
import { useDoctypeModal } from '@/composables/doctypeModal'
import { useQuoteModal } from '@/composables/quoteModal'

// Provided by Lead.vue (and any other parent that wants its resources refreshed
// when a child modal saves). null fallback for parents that don't provide it.
const reloadAfterChildModal = inject('reloadAfterChildModal', null)

const props = defineProps({
  doctype: { type: String, default: '' },
  doc: { type: Object, default: () => ({}) },
})

const activities = defineModel({ type: Object })

const { showModal } = useDoctypeModal()
const { showQuoteModal: _showQuoteModal } = useQuoteModal()
const { updateOnboardingStep } = useOnboarding('frappecrm')
const { capture } = useTelemetry()

// --- Quote flow ---

async function requestQuote(leadName) {
  if (!leadName) return
  const rows = await call('frappe.client.get_list', {
    doctype: 'CRM Quote Request',
    filters: { lead: leadName, is_superseded: 0 },
    fields: ['name', 'status'],
    order_by: 'creation desc',
    limit: 1,
  })
  const existing = rows?.find((r) => r.status !== 'Accepted')
  if (existing) {
    openQuoteModal(existing.name)
  } else {
    _showQuoteModal({
      name: '',
      leadName,
      leadDoc: props.doc,
      onUpdated: onQuoteUpdated,
    })
  }
}

function openQuoteModal(qrName) {
  _showQuoteModal({
    name: qrName,
    onUpdated: onQuoteUpdated,
  })
}

function onQuoteUpdated() {
  activities.value.reload()
  reloadAfterChildModal?.()
}

async function showQuoteRequest(
  leadName,
  _title = 'Quote Request',
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
  openQuoteModal(resolvedName)
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
