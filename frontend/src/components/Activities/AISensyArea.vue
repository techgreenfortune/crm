<template>
  <div class="px-3 sm:px-10 pb-5 space-y-3">
    <div
      v-for="msg in messages"
      :key="msg.name"
      class="flex justify-end"
    >
      <div
        class="relative max-w-[80%] rounded-md bg-surface-gray-1 p-2.5 pl-3 text-base shadow-sm"
      >
        <Badge
          v-if="msg.status === 'Failed'"
          theme="red"
          :label="__('Failed')"
          class="absolute -top-2 right-0"
        />
        <div class="font-medium text-ink-gray-8 mb-0.5">
          {{ msg.template_name }}
        </div>
        <div v-if="msg.variables" class="text-ink-gray-5 text-sm">
          {{ formatVariables(msg.variables) }}
        </div>
        <div class="mt-1 flex items-center justify-between gap-4">
          <span class="text-xs text-ink-gray-4">{{ __('To:') }} {{ msg.to }}</span>
          <Tooltip :text="formatDate(msg.creation)">
            <span class="text-xs text-ink-gray-4 whitespace-nowrap">
              {{ __(timeAgo(msg.creation)) }}
            </span>
          </Tooltip>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup>
import { Badge, Tooltip } from 'frappe-ui'
import { formatDate, timeAgo } from '@/utils'

defineProps({
  messages: { type: Array, default: () => [] },
})

function formatVariables(vars) {
  try {
    const parsed = JSON.parse(vars.replace(/'/g, '"'))
    return parsed.join(', ')
  } catch {
    return vars
  }
}
</script>
