<!--
  Reusable multi-select user picker (Popover + checkable list).

  Presentational only: it renders `users` and reflects/emits the selected
  `modelValue` array.  Callers own what selection MEANS (dashboard scope vs.
  list `lead_owner` filter) and wire the apply logic via @update:modelValue.

  Shared by Dashboard.vue (hierarchy scope) and ViewControls.vue (Lead
  owner quick filter) so the markup lives in one place.
-->
<template>
  <Popover :placement="placement">
    <template #target="{ togglePopover, isOpen }">
      <button
        type="button"
        :class="
          pill
            ? 'flex h-7 w-full items-center justify-between gap-2 rounded border border-[--surface-gray-2] bg-surface-gray-2 px-2 text-base text-ink-gray-8 hover:border-outline-gray-modals hover:bg-surface-gray-3 focus:outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-3'
            : 'flex h-8 w-full items-center justify-between gap-2 rounded-md border border-outline-gray-2 bg-surface-white px-3 text-sm text-ink-gray-8 hover:bg-surface-gray-2 focus:outline-none focus:ring-2 focus:ring-outline-gray-3'
        "
        @click="togglePopover()"
      >
        <span class="truncate">{{ label }}</span>
        <FeatherIcon
          name="chevron-down"
          class="size-4 shrink-0 text-ink-gray-5 transition-transform"
          :class="isOpen && 'rotate-180'"
        />
      </button>
    </template>
    <template #body>
      <div
        class="mt-1 max-h-72 w-64 overflow-y-auto rounded-lg border border-outline-gray-modal bg-surface-modal p-1 shadow-md"
      >
        <button
          type="button"
          class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-surface-gray-2"
          @click="clear"
        >
          <FeatherIcon
            v-if="modelValue.length === 0"
            name="check"
            class="size-4 text-ink-gray-7"
          />
          <span v-else class="size-4" />
          <span class="font-medium">{{ allLabel }}</span>
        </button>
        <button
          v-for="u in users"
          :key="u.name"
          type="button"
          class="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-surface-gray-2"
          @click="toggle(u.name)"
        >
          <FeatherIcon
            v-if="modelValue.includes(u.name)"
            name="check"
            class="size-4 text-ink-gray-7"
          />
          <span v-else class="size-4" />
          <UserAvatar :user="u.name" size="sm" />
          <Tooltip :text="u.name">
            <span class="truncate">{{ u.full_name || u.name }}</span>
          </Tooltip>
        </button>
      </div>
    </template>
  </Popover>
</template>

<script setup>
import UserAvatar from '@/components/UserAvatar.vue'
import { Popover, Tooltip, FeatherIcon } from 'frappe-ui'
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  users: { type: Array, default: () => [] },
  // Label for the "no selection" option (e.g. "All owners").
  allLabel: { type: String, default: () => __('All') },
  // Optional override for the single-selection / multi-selection summary.
  selectedLabel: { type: String, default: '' },
  placement: { type: String, default: 'bottom-start' },
  // When true, the trigger matches the "subtle" pill look used by other
  // quick filters (ViewControls). Default (false) keeps the outline look
  // used by Dashboard.vue's hierarchy scope picker.
  pill: { type: Boolean, default: false },
})

const emit = defineEmits(['update:modelValue'])

const label = computed(() => {
  const n = props.modelValue.length
  if (n === 0) return props.allLabel
  if (props.selectedLabel) return props.selectedLabel
  if (n === 1) {
    const u = props.users.find((x) => x.name === props.modelValue[0])
    return u?.full_name || props.modelValue[0]
  }
  return __('{0} users selected', [String(n)])
})

function toggle(name) {
  const next = [...props.modelValue]
  const idx = next.indexOf(name)
  if (idx === -1) next.push(name)
  else next.splice(idx, 1)
  emit('update:modelValue', next)
}

function clear() {
  emit('update:modelValue', [])
}
</script>
