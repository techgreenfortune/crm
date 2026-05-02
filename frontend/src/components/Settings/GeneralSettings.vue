<template>
  <div class="flex h-full flex-col gap-6 py-8 px-6 text-ink-gray-8">
    <div class="flex flex-col gap-1 px-2">
      <h2 class="flex gap-2 text-xl font-semibold leading-none h-5">
        {{ __('General Settings') }}
      </h2>
      <p class="text-p-base text-ink-gray-6">
        {{ __('Configure general settings for your application') }}
      </p>
    </div>

    <div class="flex-1 flex flex-col overflow-y-auto">
      <div class="flex items-center justify-between py-3 px-2">
        <div class="flex flex-col">
          <div class="text-p-base font-medium text-ink-gray-7 truncate">
            {{ __('Update timestamp on new communication') }}
          </div>
          <div class="text-p-sm text-ink-gray-5 truncate">
            {{
              __(
                'Update the modified timestamp on new email communication & comments for leads & deals',
              )
            }}
          </div>
        </div>
        <div>
          <Switch
            :model-value="
              Boolean(settings.doc.update_timestamp_on_new_communication)
            "
            size="sm"
            @update:modelValue="
              (val) => toggle('update_timestamp_on_new_communication', val)
            "
          />
        </div>
      </div>
      <div class="h-px border-t mx-2 border-outline-gray-modals" />
      <div class="flex gap-4 items-center justify-between py-3 px-2">
        <div class="flex flex-col">
          <div class="text-p-base font-medium text-ink-gray-7 truncate">
            {{ __('Mark lead/deal as replied on response') }}
          </div>
          <div class="text-p-sm text-ink-gray-5">
            {{
              __(
                'Automatically sets Communication Status to "Replied" for the lead or deal when a response is received. Applies only when SLA is enabled',
              )
            }}
          </div>
        </div>
        <div>
          <Switch
            :model-value="Boolean(settings.doc.auto_mark_replied_on_response)"
            size="sm"
            @update:modelValue="
              (val) => toggle('auto_mark_replied_on_response', val)
            "
          />
        </div>
      </div>
      <div class="h-px border-t mx-2 border-outline-gray-modals" />
      <div class="flex gap-4 items-center justify-between py-3 px-2">
        <div class="flex flex-col">
          <div class="text-p-base font-medium text-ink-gray-7 truncate">
            {{ __('Reopen lead/deal on new communication') }}
          </div>
          <div class="text-p-sm text-ink-gray-5">
            {{
              __(
                'Automatically sets Communication Status to "Open" for the lead or deal when a new communication is created. Applies only when SLA is enabled',
              )
            }}
          </div>
        </div>
        <div>
          <Switch
            :model-value="
              Boolean(settings.doc.auto_reopen_on_new_communication)
            "
            size="sm"
            @update:modelValue="
              (val) => toggle('auto_reopen_on_new_communication', val)
            "
          />
        </div>
      </div>
      <div class="h-px border-t mx-2 border-outline-gray-modals" />
      <div class="flex gap-4 items-center justify-between py-3 px-2">
        <div class="flex flex-col">
          <div class="text-p-base font-medium text-ink-gray-7 truncate">
            {{ __('Enable OpsGate') }}
          </div>
          <div class="text-p-sm text-ink-gray-5">
            {{ __('Show OpsGate link in the sidebar') }}
          </div>
        </div>
        <div>
          <Switch
            :model-value="Boolean(settings.doc.opsgate_enabled)"
            size="sm"
            @update:modelValue="(val) => toggle('opsgate_enabled', val)"
          />
        </div>
      </div>
      <div v-show="Boolean(settings.doc?.opsgate_enabled)">
        <div class="h-px border-t mx-2 border-outline-gray-modals" />
        <div class="flex gap-4 items-center justify-between py-3 px-2">
          <div class="flex flex-col shrink-0">
            <div class="text-p-base font-medium text-ink-gray-7">
              {{ __('OpsGate URL') }}
            </div>
            <div class="text-p-sm text-ink-gray-5">
              {{ __('URL of the OpsGate instance for this environment') }}
            </div>
          </div>
          <div class="flex items-center gap-2 w-96">
            <FormControl
              v-model="settings.doc.opsgate_url"
              type="text"
              placeholder="https://opsgate.example.com"
              class="flex-1"
              @input="urlSaved = false"
            />
            <Button
              variant="subtle"
              :label="urlSaved ? __('Saved') : __('Save')"
              :icon-left="urlSaved ? 'check' : null"
              :loading="settings.setValue.loading"
              class="shrink-0 transition-all"
              :class="urlSaved ? 'text-green-600' : ''"
              @click="saveOpsGateUrl"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { opsGateEnabled, opsGateUrl } from '@/composables/settings'
import { getSettings } from '@/stores/settings'
import { Switch, toast } from 'frappe-ui'

const { _settings: settings } = getSettings()
const urlSaved = ref(false)

function toggle(settingKey, val) {
  const newVal = val ? 1 : 0
  settings.setValue.submit(
    { [settingKey]: newVal },
    {
      onSuccess: () => {
        // frappe.client.set_value response omits opsgate_* fields, so patch them back
        settings.doc[settingKey] = newVal
        if (settingKey === 'opsgate_enabled') {
          opsGateEnabled.value = Boolean(val)
          toast.success(val ? __('OpsGate enabled') : __('OpsGate disabled'))
        } else {
          toast.success(
            val
              ? __('Setting enabled successfully')
              : __('Setting disabled successfully'),
          )
        }
      },
      onError: () => {
        settings.doc[settingKey] = newVal ? 0 : 1
      },
    },
  )
}

function saveOpsGateUrl() {
  const url = settings.doc.opsgate_url || ''
  settings.setValue.submit(
    { opsgate_url: url },
    {
      onSuccess: () => {
        // patch back since server response omits opsgate_* fields
        settings.doc.opsgate_url = url
        settings.doc.opsgate_enabled = 1
        opsGateUrl.value = url
        urlSaved.value = true
        setTimeout(() => (urlSaved.value = false), 3000)
        toast.success(__('OpsGate URL saved'))
      },
    },
  )
}
</script>
