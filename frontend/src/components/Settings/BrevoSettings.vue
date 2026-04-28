<template>
  <SettingsLayoutBase>
    <template #title>
      <div class="flex gap-1 items-center">
        <span class="font-semibold text-xl text-ink-gray-9">
          {{ __('Brevo Settings') }}
        </span>
        <Badge
          v-if="brevo.doc?.enabled && isDirty"
          :label="__('Not Saved')"
          variant="subtle"
          theme="orange"
        />
      </div>
    </template>
    <template #header-actions>
      <div v-if="brevo.doc?.enabled && !brevo.get.loading" class="flex gap-2">
        <Button
          v-if="isDirty"
          :label="__('Discard Changes')"
          variant="subtle"
          @click="brevo.reload()"
        />
        <Button :label="__('Disable')" variant="subtle" @click="disable" />
        <Button
          variant="solid"
          :label="__('Update')"
          :loading="brevo.save.loading"
          :disabled="!isDirty"
          @click="update"
        />
      </div>
    </template>
    <template #content>
      <div v-if="brevo.doc" class="h-full">
        <div v-if="brevo.doc.enabled" class="space-y-4">
          <div class="grid grid-cols-2 gap-4">
            <Password
              v-model="brevo.doc.api_key"
              :label="__('API Key')"
              placeholder="xkeysib-..."
              required
            />
            <FormControl
              v-model="brevo.doc.sender_email"
              :label="__('Sender Email')"
              type="email"
              placeholder="crm@yourdomain.com"
              required
              autocomplete="off"
            />
            <FormControl
              v-model="brevo.doc.sender_name"
              :label="__('Sender Name')"
              type="text"
              placeholder="Frappe CRM"
              autocomplete="off"
            />
          </div>
          <div class="h-px border-t border-outline-gray-modals" />
          <div class="flex items-center justify-between gap-4">
            <div class="flex flex-col">
              <div class="text-p-sm text-ink-gray-5">
                {{
                  __(
                    'Get your API key from Brevo → Settings → API Keys. Make sure the sender email is verified in your Brevo account.',
                  )
                }}
              </div>
            </div>
            <Button
              :label="__('Send Test Email')"
              variant="subtle"
              icon-left="send"
              :loading="sendTest.loading"
              @click="sendTestEmail"
            />
          </div>
        </div>
        <!-- Disabled state -->
        <div v-else class="relative flex h-full w-full justify-center">
          <div
            class="absolute left-1/2 flex w-72 -translate-x-1/2 flex-col items-center gap-3"
            :style="{ top: '35%' }"
          >
            <div class="flex flex-col items-center gap-1.5 text-center">
              <Email2Icon class="size-7.5 text-ink-gray-7" />
              <span class="text-lg font-medium text-ink-gray-8">
                {{ __('Brevo Integration Disabled') }}
              </span>
              <span class="text-center text-p-base text-ink-gray-6">
                {{
                  __(
                    'Enable Brevo to send transactional emails like invitations directly from your CRM',
                  )
                }}
              </span>
              <Button :label="__('Enable')" variant="solid" @click="enable" />
            </div>
          </div>
        </div>
      </div>
      <div
        v-else-if="brevo.get.loading"
        class="flex items-center justify-center mt-[35%]"
      >
        <LoadingIndicator class="size-6" />
      </div>
    </template>
  </SettingsLayoutBase>
</template>
<script setup>
import Email2Icon from '@/components/Icons/Email2Icon.vue'
import { brevoEnabled } from '@/composables/settings'
import { useDocument } from '@/data/document'
import { usersStore } from '@/stores/users'
import { createResource, toast } from 'frappe-ui'
import { computed } from 'vue'

const { document: brevo } = useDocument(
  'CRM Brevo Settings',
  'CRM Brevo Settings',
)
const { getUser } = usersStore()

const sendTest = createResource({
  url: 'crm.integrations.brevo.api.send_test_email',
  onSuccess: (data) => {
    toast.success(data.message)
  },
  onError: (err) => {
    toast.error(err.messages?.[0] || err.message || 'Failed to send test email')
  },
})

function sendTestEmail() {
  if (isDirty.value) {
    toast.warning('Save your settings first before sending a test email')
    return
  }
  const me = getUser()?.email
  if (!me || !me.includes('@')) return
  sendTest.submit({ to_email: me })
}

function enable() {
  brevo.doc.enabled = true
}

function disable() {
  brevo.doc.enabled = false
  update()
}

function update() {
  brevo.save.submit(null, {
    onSuccess: () => brevo.reload(),
  })
  brevoEnabled.value = brevo.doc.enabled
}

const isDirty = computed(() => {
  return (
    brevo.doc &&
    brevo.originalDoc &&
    JSON.stringify(brevo.doc) !== JSON.stringify(brevo.originalDoc)
  )
})
</script>
