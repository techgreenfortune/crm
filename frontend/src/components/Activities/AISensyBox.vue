<template>
  <div class="flex flex-col gap-2 px-3 py-2.5 sm:px-10" v-bind="$attrs">
    <div
      v-if="!doc.mobile_no"
      class="flex items-center gap-1.5 text-sm text-ink-red-4"
    >
      <FeatherIcon name="alert-circle" class="size-3.5 shrink-0" />
      {{ __('No mobile number on this record. Please add one first.') }}
    </div>
    <div class="flex items-end gap-2">
      <div class="flex flex-1 flex-col gap-1.5">
        <FormControl
          v-model="templateName"
          type="select"
          :options="templateOptions"
          :placeholder="__('Select a template')"
          class="w-full"
        />
        <Textarea
          v-model="variablesText"
          :rows="variablesText ? 3 : 1"
          :placeholder="__('Variables (one per line, in order)…')"
          class="w-full text-sm"
          @focus="expandVariables = true"
          @blur="expandVariables = variablesText.length > 0"
        />
      </div>
      <Button
        variant="solid"
        icon="send"
        :loading="sending"
        :disabled="!templateName || !doc.mobile_no"
        @click="send"
      />
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
const expandVariables = ref(false)

const templatesResource = createResource({
  url: 'crm.integrations.aisensy.api.get_templates',
  cache: 'aisensy_templates',
  auto: true,
})

const templateOptions = computed(() => {
  const list = templatesResource.data || []
  return [
    { label: __('Select a template'), value: '' },
    ...list.map((t) => ({ label: t, value: t })),
  ]
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
    toast.success(__('WhatsApp message sent'))
    templateName.value = ''
    variablesText.value = ''
    aisensyMessages.value.reload()
  } catch (err) {
    toast.error(err.messages?.[0] || __('Failed to send message'))
  } finally {
    sending.value = false
  }
}
</script>
