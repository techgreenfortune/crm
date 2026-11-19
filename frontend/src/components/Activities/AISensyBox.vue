<template>
  <div class="flex flex-col gap-3 px-3 py-3 sm:px-10" v-bind="$attrs">
    <div>
      <div class="mb-1 text-sm font-medium text-ink-gray-7">
        {{ __('Template') }}
      </div>
      <Autocomplete
        :model-value="templateName"
        :options="templateOptions"
        :placeholder="__('Select a template')"
        @update:modelValue="templateName = $event?.value || ''"
      />
    </div>
    <div class="flex justify-end">
      <Button
        variant="solid"
        :label="__('Send')"
        icon-left="send"
        :loading="sending"
        :disabled="!templateName || !doc.mobile_no"
        @click="send"
      />
    </div>
    <div v-if="!doc.mobile_no" class="text-sm text-ink-red-4">
      {{ __('No mobile number found on this record. Please add one first.') }}
    </div>
  </div>
</template>
<script setup>
import { Autocomplete, call, createResource, toast } from 'frappe-ui'
import { computed, ref } from 'vue'

const props = defineProps({
  doctype: { type: String, default: '' },
})

const doc = defineModel({ type: Object, default: () => ({}) })
const aisensyMessages = defineModel('aisensyMessages', {
  type: Object,
  default: () => ({}),
})

const templateName = ref('')
const sending = ref(false)

const templatesResource = createResource({
  url: 'crm.integrations.aisensy.api.get_templates',
  cache: 'aisensy_templates',
  auto: true,
})

const templateOptions = computed(() => {
  const list = templatesResource.data || []
  return list.map((t) => ({ label: t, value: t }))
})

async function send() {
  if (!templateName.value || !doc.value.mobile_no) return

  sending.value = true
  try {
    await call('crm.integrations.aisensy.api.send_message', {
      reference_doctype: props.doctype,
      reference_name: doc.value.name,
      to: doc.value.mobile_no,
      template_name: templateName.value,
    })
    toast.success(__('WhatsApp message sent'))
    templateName.value = ''
    aisensyMessages.value.reload()
  } catch (err) {
    toast.error(err.messages?.[0] || __('Failed to send message'))
  } finally {
    sending.value = false
  }
}
</script>
