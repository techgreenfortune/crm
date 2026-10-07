<template>
  <Dialog v-model="show" :options="{ title: __('Add Disposition') }">
    <template #body-content>
      <div class="flex flex-col gap-3">
        <div>
          <div class="mb-2 text-sm text-ink-gray-5">
            {{ __('Disposition') }}
            <span class="text-ink-red-2">*</span>
          </div>
          <FormControl
            v-model="disposition"
            type="select"
            :options="dispositionOptions"
          />
        </div>
        <div v-if="selected?.requires_callback_datetime">
          <div class="mb-2 text-sm text-ink-gray-5">
            {{ __('Scheduled Callback At') }}
            <span class="text-ink-red-2">*</span>
          </div>
          <FormControl
            v-model="scheduledCallbackAt"
            type="datetime-local"
            :min="callbackMin"
          />
        </div>
        <template v-if="selected?.requires_routing_reason">
          <div>
            <div class="mb-2 text-sm text-ink-gray-5">
              {{ __('Routing Reason') }}
              <span class="text-ink-red-2">*</span>
            </div>
            <FormControl
              v-model="routingReason"
              type="select"
              :options="reasonOptions"
            />
          </div>
          <div>
            <div class="mb-2 text-sm text-ink-gray-5">
              {{ __('Partner Fabricator Name') }}
              <span class="text-ink-red-2">*</span>
            </div>
            <FormControl v-model="partnerFabricatorName" type="text" />
          </div>
          <div v-if="routingReason === 'Other'">
            <div class="mb-2 text-sm text-ink-gray-5">
              {{ __('Routing Notes') }}
              <span class="text-ink-red-2">*</span>
            </div>
            <FormControl v-model="routingNotes" type="textarea" />
          </div>
        </template>
      </div>
    </template>
    <template #actions>
      <div class="flex items-center justify-between gap-2">
        <ErrorMessage class="min-w-0" :message="error" />
        <div class="flex shrink-0 gap-2">
          <Button :label="__('Cancel')" @click="show = false" />
          <Button
            variant="solid"
            :label="__('Save')"
            :loading="saving"
            @click="save"
          />
        </div>
      </div>
    </template>
  </Dialog>
</template>
<script setup>
import {
  Dialog,
  FormControl,
  ErrorMessage,
  call,
  createResource,
  toast,
} from 'frappe-ui'
import { computed, ref, watch } from 'vue'

const NO_ANSWER = 'No Answer / Not Reachable'

const props = defineProps({
  callLog: { type: Object, required: true },
})

const emit = defineEmits(['saved'])

const show = defineModel({ type: Boolean })

const disposition = ref('')
const scheduledCallbackAt = ref('')
const routingReason = ref('')
const partnerFabricatorName = ref('')
const routingNotes = ref('')
const error = ref('')
const saving = ref(false)

const dispositions = createResource({
  url: 'frappe.client.get_list',
  params: {
    doctype: 'CRM Call Disposition',
    filters: { enabled: 1 },
    fields: [
      'name',
      'label',
      'requires_callback_datetime',
      'requires_routing_reason',
    ],
    order_by: 'position asc',
    limit_page_length: 50,
  },
  cache: 'crm-call-dispositions',
  auto: true,
})

// Same choices as the call popup: an answered call can't be "no answer", any other outcome only can.
const dispositionOptions = computed(() => [
  { label: __('Select a disposition'), value: '' },
  ...(dispositions.data || [])
    .filter((d) =>
      props.callLog.status === 'Completed'
        ? d.name !== NO_ANSWER
        : d.name === NO_ANSWER,
    )
    .map((d) => ({ label: d.label || d.name, value: d.name })),
])

const selected = computed(() =>
  (dispositions.data || []).find((d) => d.name === disposition.value),
)

const reasonOptions = [
  { label: __('Select a reason'), value: '' },
  { label: __('Price Mismatch'), value: 'Price Mismatch' },
  { label: __('GST Issue'), value: 'GST Issue' },
  { label: __('Serviceability'), value: 'Serviceability' },
  { label: __('Other'), value: 'Other' },
]

function pad(n) {
  return String(n).padStart(2, '0')
}

const callbackMin = computed(() => {
  const now = new Date()
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`
})

watch(show, (open) => {
  if (!open) return
  disposition.value = ''
  scheduledCallbackAt.value = ''
  routingReason.value = ''
  partnerFabricatorName.value = ''
  routingNotes.value = ''
  error.value = ''
})

function validationError() {
  if (!disposition.value) return __('Select a disposition')
  if (selected.value?.requires_callback_datetime && !scheduledCallbackAt.value)
    return __('Set the scheduled callback datetime')
  if (selected.value?.requires_routing_reason) {
    if (!routingReason.value || !partnerFabricatorName.value)
      return __('Fill in Routing Reason and Partner Fabricator Name')
    if (routingReason.value === 'Other' && !routingNotes.value)
      return __('Routing Notes are required when reason is "Other"')
  }
  return ''
}

async function save() {
  error.value = validationError()
  if (error.value) return
  saving.value = true
  try {
    await call('crm.integrations.api.add_disposition_to_call_log', {
      call_sid: props.callLog.name,
      disposition: disposition.value,
      scheduled_callback_at: scheduledCallbackAt.value || null,
      fabricator_routing_reason: routingReason.value || null,
      partner_fabricator_name: partnerFabricatorName.value || null,
      fabricator_routing_notes: routingNotes.value || null,
    })
    toast.success(__('Disposition saved'))
    show.value = false
    emit('saved')
  } catch (err) {
    error.value =
      err?.messages?.[0] || err?.message || __('Failed to save disposition')
  } finally {
    saving.value = false
  }
}
</script>
