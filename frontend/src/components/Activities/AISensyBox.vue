<template>
  <div class="flex flex-col gap-3 px-3 py-3 sm:px-10" v-bind="$attrs">
    <FormControl
      v-model="templateName"
      :label="__('Template')"
      type="select"
      :options="templateOptions"
      :placeholder="__('Select a template')"
    />
    <div>
      <div class="mb-1 text-sm font-medium text-ink-gray-7">
        {{ __('Variables') }}
        <span class="text-ink-gray-4 font-normal">
          {{ __('(one per line, in order)') }}
        </span>
      </div>
      <Textarea
        v-model="variablesText"
        :rows="3"
        :placeholder="__('John Doe\nDemo Meeting\n28 April 2026')"
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
import { call, createResource, Textarea, toast } from 'frappe-ui'
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
const variablesText = ref('')
const sending = ref(false)

const templatesResource = createResource({
  url: 'crm.integrations.aisensy.api.get_templates',
  cache: 'aisensy_templates',
  auto: true,
})

const templateOptions = computed(() => {
  const list = templatesResource.data || []
  return [{ label: __('Select a template'), value: '' }, ...list.map((t) => ({ label: t, value: t }))]
})

async function send() {
  if (!templateName.value || !doc.value.mobile_no) return
  const variables = variablesText.value
    .split('\n')
    .map((v) => v.trim())
    .filter(Boolean)

  sending.value = true
  try {
    await call('crm.integrations.aisensy.api.send_message', {
      reference_doctype: props.doctype,
      reference_name: doc.value.name,
      to: doc.value.mobile_no,
      template_name: templateName.value,
      variables,
    })
    toast.success('WhatsApp message sent')
    templateName.value = ''
    variablesText.value = ''
    aisensyMessages.value.reload()
  } catch (err) {
    toast.error(err.messages?.[0] || 'Failed to send message')
  } finally {
    sending.value = false
  }
}
</script>
