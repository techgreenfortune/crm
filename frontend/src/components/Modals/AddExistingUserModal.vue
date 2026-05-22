<template>
  <Dialog
    v-model="show"
    :options="{ title: __('Add Existing User') }"
    @close="show = false"
  >
    <template #body-content>
      <div class="flex gap-1 border rounded mb-4 p-2 text-ink-gray-5">
        <FeatherIcon name="info" class="size-3.5" />
        <p class="text-sm">
          {{
            __(
              'Add existing system users to this CRM. Assign them a role to grant access with their current credentials.',
            )
          }}
        </p>
      </div>

      <label class="block text-xs text-ink-gray-5 mb-1.5">
        {{ __('Users') }}
      </label>

      <div class="p-2 group bg-surface-gray-2 hover:bg-surface-gray-3 rounded">
        <MultiSelectUserInput
          v-if="users?.data?.crmUsers?.length"
          v-model="newUsers"
          class="flex-1"
          inputClass="!bg-surface-gray-2 hover:!bg-surface-gray-3 group-hover:!bg-surface-gray-3"
          :placeholder="__('john@doe.com')"
          :validate="validateEmail"
          :existingEmails="[
            ...users.data.crmUsers.map((user) => user.name),
            'admin@example.com',
          ]"
          :error-message="
            (value) => __('{0} is an invalid email address', [value])
          "
        />
      </div>
      <FormControl
        v-model="role"
        type="select"
        class="mt-4"
        :label="__('Role')"
        :options="roleOptions"
        :description="description"
      />
    </template>
    <template #actions>
      <div class="flex justify-end gap-2">
        <Button
          variant="solid"
          :label="__('Add')"
          :disabled="!newUsers.length"
          :loading="addNewUser.loading"
          @click="addNewUser.submit()"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import MultiSelectUserInput from '@/components/Controls/MultiSelectUserInput.vue'
import { validateEmail } from '@/utils'
import { usersStore } from '@/stores/users'
import { createResource, toast } from 'frappe-ui'
import { ref, computed } from 'vue'

const { users, isAdmin } = usersStore()

const show = defineModel({ type: Boolean })

// CRM role profiles (must match crm/fixtures/role_profile.json) + System Manager.
const CRM_ROLES = [
  'Sales Head',
  'RSM',
  'ASM',
  'Sales Executive',
  'Project Sales Executive',
  'Sales Coordinator',
  'Marketing',
  'Calling Team',
  'Jr. Sales Executive',
  'B2F Team',
  'Estimation Team',
  'Management',
]

const newUsers = ref([])
const role = ref('Sales Executive')

const description = computed(() => {
  const descriptions = {
    'System Manager':
      'Can manage all aspects of the CRM, including user management, customizations and settings.',
    'Sales Head':
      'Full access across all areas, regions, and retail + projects pipelines. Can invite and manage users.',
    'Sales Coordinator':
      'Edit access across all leads (all types, all stages). Operational support.',
    'RSM': 'Full access to Project leads in their assigned region and downstream team.',
    'ASM': 'Full access to retail leads in their assigned area and downstream team.',
    'Sales Executive': 'Full access to retail leads assigned to them.',
    'Project Sales Executive': 'Full access to Project leads assigned to them.',
    'Marketing': 'Read access to all leads; write access to source/campaign fields.',
    'Calling Team': 'Create/edit leads up to C2; log calls; set dispositions.',
    'Jr. Sales Executive': 'Create/edit leads up to C2; view-only after C2.',
    'B2F Team': 'Access only to leads at C7. Updates status, partner fabricator tag.',
    'Estimation Team': 'Access only to C2 leads. Uploads quote files.',
    'Management': 'Read-only access across all pipelines and dashboards.',
  }
  return descriptions[role.value] || ''
})

const roleOptions = computed(() => {
  return [
    ...CRM_ROLES.map((r) => ({ value: r, label: __(r) })),
    ...(isAdmin() ? [{ value: 'System Manager', label: __('Admin') }] : []),
  ]
})

const addNewUser = createResource({
  url: 'crm.api.user.add_existing_users',
  makeParams: () => ({
    users: JSON.stringify(newUsers.value),
    role: role.value,
  }),
  onSuccess: () => {
    toast.success(__('Users Added Successfully'))
    newUsers.value = []
    show.value = false
    users.reload()
  },
  onError: (error) => {
    toast.error(error.messages[0] || __('Failed to Add Users'))
  },
})
</script>
