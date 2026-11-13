<template>
  <div class="flex h-full flex-col gap-6 py-8 px-6 text-ink-gray-8">
    <div class="flex justify-between px-2 text-ink-gray-8">
      <div class="flex flex-col gap-1">
        <h2 class="flex gap-2 text-xl font-semibold leading-none h-5">
          {{ __('Restricted Fields') }}
        </h2>
        <p class="text-p-base text-ink-gray-6">
          {{
            __(
              'Hide sensitive fields from specific roles or users. Fields are visible to everyone unless a rule below hides them.',
            )
          }}
        </p>
      </div>
      <div class="flex item-center space-x-2 w-3/12 justify-end">
        <Button
          v-if="isDirty"
          :label="__('Update')"
          variant="solid"
          :loading="saveRules.loading"
          @click="save"
        />
      </div>
    </div>

    <div class="flex-1 flex flex-col overflow-y-auto px-2">
      <p
        v-if="settings.data?.exempt_roles?.length"
        class="text-p-sm text-ink-gray-5 pb-3"
      >
        {{ __('Always visible to: {0}', [exemptRolesLabel]) }}
      </p>

      <div
        v-if="rules.length"
        class="grid grid-cols-[1fr_8rem_1fr_2rem] gap-3 items-center pb-2 text-p-sm text-ink-gray-5"
      >
        <div>{{ __('Field Group') }}</div>
        <div>{{ __('Applies To') }}</div>
        <div>{{ __('Role / User') }}</div>
        <div />
      </div>
      <div
        v-for="(rule, i) in rules"
        :key="i"
        class="grid grid-cols-[1fr_8rem_1fr_2rem] gap-3 items-center py-1.5"
      >
        <Select v-model="rule.field_group" :options="fieldGroupOptions" />
        <Select
          v-model="rule.applies_to"
          :options="['Role', 'User']"
          @update:modelValue="clearTarget(rule)"
        />
        <Link
          v-if="rule.applies_to === 'User'"
          v-model="rule.user"
          doctype="User"
          :placeholder="__('Select user')"
        />
        <Link
          v-else
          v-model="rule.role"
          doctype="Role"
          :placeholder="__('Select role')"
        />
        <Button variant="ghost" icon="x" @click="rules.splice(i, 1)" />
      </div>
      <div
        v-if="!rules.length && !settings.loading"
        class="text-p-base text-ink-gray-5 py-3"
      >
        {{ __('No rules. All fields are visible to everyone.') }}
      </div>
      <div class="pt-3">
        <Button :label="__('Add Rule')" icon-left="plus" @click="addRule" />
      </div>
    </div>
  </div>
</template>

<script setup>
import Link from '@/components/Controls/Link.vue'
import { Select, Button, toast, createResource } from 'frappe-ui'
import { ref, computed } from 'vue'

const rules = ref([])
const originalRules = ref('[]')

const settings = createResource({
  url: 'crm.permissions.field_visibility.get_restricted_field_settings',
  auto: true,
  onSuccess: load,
})

const saveRules = createResource({
  url: 'crm.permissions.field_visibility.save_restricted_field_rules',
})

function load(data) {
  rules.value = (data?.rules || []).map((r) => ({ ...r }))
  originalRules.value = JSON.stringify(rules.value)
}

const fieldGroupOptions = computed(() => settings.data?.field_groups || [])
const exemptRolesLabel = computed(() =>
  (settings.data?.exempt_roles || []).join(', '),
)
const isDirty = computed(
  () => JSON.stringify(rules.value) !== originalRules.value,
)

function addRule() {
  rules.value.push({
    field_group: fieldGroupOptions.value[0] || '',
    applies_to: 'Role',
    role: '',
    user: '',
  })
}

function clearTarget(rule) {
  rule.role = ''
  rule.user = ''
}

function save() {
  saveRules.submit(
    { rules: rules.value },
    {
      onSuccess(data) {
        settings.setData(data)
        load(data)
        toast.success(__('Settings updated successfully'))
      },
      onError(error) {
        toast.error(error?.messages?.[0] || __('Failed to save settings'))
      },
    },
  )
}
</script>
