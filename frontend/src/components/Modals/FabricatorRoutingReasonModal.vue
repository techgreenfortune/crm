<template>
  <Dialog
    v-model="show"
    :options="{ title: __('Fabricator Routing Reason') }"
    @close="cancel"
  >
    <template #body-content>
      <div class="-mt-3 mb-4 text-p-base text-ink-gray-7">
        {{
          __('Please provide a reason for routing this {0} to the fabricator', [
            doctype.toLowerCase().replace('crm ', ''),
          ])
        }}
      </div>
      <div class="flex flex-col gap-3">
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
            {{ __('Routing Notes') }}
            <span v-if="routingReason === 'Other'" class="text-ink-red-2"
              >*</span
            >
          </div>
          <FormControl
            class="form-control flex-1 truncate"
            type="textarea"
            :value="routingNotes"
            @change="(e) => (routingNotes = e.target.value)"
          />
        </div>
      </div>
    </template>
    <template #actions>
      <div class="flex justify-between items-center gap-2">
        <div><ErrorMessage :message="error" /></div>
        <div class="flex gap-2">
          <Button :label="__('Cancel')" @click="cancel" />
          <Button variant="solid" :label="__('Save')" @click="save" />
        </div>
      </div>
    </template>
  </Dialog>
</template>
<script setup>
import { Dialog, FormControl, ErrorMessage } from 'frappe-ui'
import { ref } from 'vue'

const props = defineProps({
  doctype: { type: String, default: 'CRM Lead' },
  document: { type: Object, required: true },
})

const show = defineModel({ type: Boolean })

const doc = props.document.doc
const routingReason = ref(doc.custom_fabricator_routing_reason || '')
const routingNotes = ref(doc.custom_fabricator_routing_notes || '')
const error = ref('')

const reasonOptions = [
  { label: __('Select a reason'), value: '' },
  { label: __('Price Mismatch'), value: 'Price Mismatch' },
  { label: __('GST Issue'), value: 'GST Issue' },
  { label: __('Serviceability'), value: 'Serviceability' },
  { label: __('Other'), value: 'Other' },
]

function cancel() {
  show.value = false
  error.value = ''
  routingReason.value = ''
  routingNotes.value = ''
  doc.status = props.document.originalDoc.status
}

function save() {
  if (!routingReason.value) {
    error.value = __('Fabricator Routing Reason is required')
    return
  }
  if (routingReason.value === 'Other' && !routingNotes.value) {
    error.value = __('Notes are required when reason is "Other"')
    return
  }

  error.value = ''
  show.value = false

  doc.custom_fabricator_routing_reason = routingReason.value
  doc.custom_fabricator_routing_notes = routingNotes.value
  props.document.save.submit()
}
</script>
