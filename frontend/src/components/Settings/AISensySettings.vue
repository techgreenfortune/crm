<template>
  <SettingsLayoutBase>
    <template #title>
      <div class="flex gap-1 items-center">
        <span class="font-semibold text-xl text-ink-gray-9">
          {{ __('AISensy Settings') }}
        </span>
        <Badge
          v-if="aisensy.doc?.enabled && isDirty"
          :label="__('Not Saved')"
          variant="subtle"
          theme="orange"
        />
      </div>
    </template>
    <template #header-actions>
      <div v-if="aisensy.doc?.enabled && !aisensy.get.loading" class="flex gap-2">
        <Button
          v-if="isDirty"
          :label="__('Discard Changes')"
          variant="subtle"
          @click="aisensy.reload()"
        />
        <Button :label="__('Disable')" variant="subtle" @click="disable" />
        <Button
          variant="solid"
          :label="__('Update')"
          :loading="aisensy.save.loading"
          :disabled="!isDirty"
          @click="update"
        />
      </div>
    </template>
    <template #content>
      <div v-if="aisensy.doc" class="h-full">
        <div v-if="aisensy.doc.enabled" class="space-y-4">
          <div class="grid grid-cols-2 gap-4">
            <Password
              v-model="aisensy.doc.api_key"
              :label="__('API Key')"
              placeholder="••••••••••••••••"
              required
            />
            <FormControl
              v-model="aisensy.doc.project_id"
              :label="__('Project ID / Username')"
              type="text"
              placeholder="your-project-id"
              required
              autocomplete="off"
            />
          </div>
          <div class="h-px border-t border-outline-gray-modals" />
          <div class="text-p-sm text-ink-gray-5">
            {{
              __(
                'Get your API Key and Project ID from AISensy → Developer → API. The Project ID is the userName field shown in your campaign API.',
              )
            }}
          </div>
        </div>
        <!-- Disabled state -->
        <div v-else class="relative flex h-full w-full justify-center">
          <div
            class="absolute left-1/2 flex w-72 -translate-x-1/2 flex-col items-center gap-3"
            :style="{ top: '35%' }"
          >
            <div class="flex flex-col items-center gap-1.5 text-center">
              <WhatsAppIcon class="size-7.5 text-ink-gray-7" />
              <span class="text-lg font-medium text-ink-gray-8">
                {{ __('AISensy Integration Disabled') }}
              </span>
              <span class="text-center text-p-base text-ink-gray-6">
                {{
                  __(
                    'Enable AISensy to send WhatsApp template messages to leads and contacts directly from the CRM',
                  )
                }}
              </span>
              <Button :label="__('Enable')" variant="solid" @click="enable" />
            </div>
          </div>
        </div>
      </div>
      <div
        v-else-if="aisensy.get.loading"
        class="flex items-center justify-center mt-[35%]"
      >
        <LoadingIndicator class="size-6" />
      </div>
    </template>
  </SettingsLayoutBase>
</template>
<script setup>
import WhatsAppIcon from '@/components/Icons/WhatsAppIcon.vue'
import { aisensyEnabled } from '@/composables/settings'
import { useDocument } from '@/data/document'
import { computed } from 'vue'

const { document: aisensy } = useDocument(
  'CRM AISensy Settings',
  'CRM AISensy Settings',
)

function enable() {
  aisensy.doc.enabled = true
}

function disable() {
  aisensy.doc.enabled = false
  update()
}

function update() {
  aisensy.save.submit(null, {
    onSuccess: () => aisensy.reload(),
  })
  aisensyEnabled.value = aisensy.doc.enabled
}

const isDirty = computed(() => {
  return (
    aisensy.doc &&
    aisensy.originalDoc &&
    JSON.stringify(aisensy.doc) !== JSON.stringify(aisensy.originalDoc)
  )
})
</script>
