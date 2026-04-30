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
    <div>
      <div class="mb-1 text-sm font-medium text-ink-gray-7">
        {{ __('Variables') }}
      </div>
      <div class="space-y-2">
        <div
          v-for="(row, idx) in variables"
          :key="idx"
          class="flex items-center gap-2"
        >
          <span class="text-xs text-ink-gray-4 w-4 text-right shrink-0">
            {{ idx + 1 }}
          </span>
          <FormControl
            v-model="row.value"
            type="text"
            :placeholder="__('Value')"
            class="flex-1"
          />
          <Button variant="ghost" icon="x" @click="removeVariable(idx)" />
        </div>
        <Button
          variant="subtle"
          icon-left="plus"
          :label="__('Add Variable')"
          @click="addVariable"
        />
      </div>
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
const variables = ref([])
const sending = ref(false)

function addVariable() {
  variables.value.push({ value: '' })
}

function removeVariable(idx) {
  variables.value.splice(idx, 1)
}

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
  const variableList = variables.value
    .map((v) => v.value.trim())
    .filter(Boolean)

  sending.value = true
  try {
    await call('crm.integrations.aisensy.api.send_message', {
      reference_doctype: props.doctype,
      reference_name: doc.value.name,
      to: doc.value.mobile_no,
      template_name: templateName.value,
      variables: variableList,
    })
    toast.success(__('WhatsApp message sent'))
    templateName.value = ''
    variables.value = []
    aisensyMessages.value.reload()
  } catch (err) {
    toast.error(err.messages?.[0] || __('Failed to send message'))
  } finally {
    sending.value = false
  }
}
</script>
