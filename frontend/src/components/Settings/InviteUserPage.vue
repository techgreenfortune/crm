<template>
  <div class="flex h-full flex-col gap-6 py-8 px-6 text-ink-gray-8">
    <div class="flex px-2 justify-between">
      <div class="flex flex-col gap-1 w-9/12">
        <h2 class="flex gap-2 text-xl font-semibold leading-none h-5">
          {{ __('Send Invites To') }}
        </h2>
        <p class="text-p-base text-ink-gray-6">
          {{
            __(
              'Invite users to access CRM. Specify their roles to control access and permissions',
            )
          }}
        </p>
      </div>
      <div class="flex item-center space-x-2 w-3/12 justify-end">
        <Button
          :label="__('Send Invites')"
          variant="solid"
          :disabled="
            !invitees.length || userExistMessage || inviteeExistMessage
          "
          :loading="inviteByEmail.loading"
          @click="inviteByEmail.submit()"
        />
      </div>
    </div>
    <div class="flex-1 flex flex-col px-2 gap-8 overflow-y-auto">
      <div>
        <FormControl
          type="textarea"
          :label="__('Invite By Email')"
          placeholder="user1@example.com, user2@example.com, ..."
          :debounce="100"
          :disabled="inviteByEmail.loading"
          :description="
            __(
              'You can invite multiple users by comma separating their email addresses',
            )
          "
          @input="updateInvitees($event.target.value)"
        />
        <div
          v-if="userExistMessage || inviteeExistMessage"
          class="text-xs text-ink-red-3 mt-1.5"
        >
          {{ userExistMessage || inviteeExistMessage }}
        </div>
        <FormControl
          v-model="role"
          type="select"
          class="mt-4"
          :label="__('Invite As')"
          :options="roleOptions"
          :description="description"
        />
      </div>
      <template v-if="pendingInvitations.data?.length && !invitees.length">
        <div class="flex flex-col gap-4">
          <div
            class="flex items-center justify-between text-base font-semibold"
          >
            <div>{{ __('Pending Invites') }}</div>
          </div>
          <ul class="flex flex-col gap-1">
            <li
              v-for="user in pendingInvitations.data"
              :key="user.name"
              class="flex items-center justify-between px-2 py-1 rounded-lg bg-surface-gray-2"
            >
              <div class="text-base">
                <span class="text-ink-gray-8">
                  {{ user.email }}
                </span>
                <span class="text-ink-gray-5">
                  ({{ roleMap[user.role] }})
                </span>
              </div>
              <div>
                <Button
                  :tooltip="__('Delete Invitation')"
                  icon="x"
                  variant="ghost"
                  :loading="
                    pendingInvitations.delete.loading &&
                    pendingInvitations.delete.params.name === user.name
                  "
                  @click="pendingInvitations.delete.submit(user.name)"
                />
              </div>
            </li>
          </ul>
        </div>
      </template>
    </div>
    <ErrorMessage :message="error" />
  </div>
</template>
<script setup>
import { validateEmail, convertArrayToString } from '@/utils'
import { usersStore } from '@/stores/users'
import { useOnboarding, useTelemetry } from 'frappe-ui/frappe'
import {
  toast,
  createListResource,
  createResource,
  FormControl,
} from 'frappe-ui'
import { ref, computed } from 'vue'

const { updateOnboardingStep } = useOnboarding('frappecrm')
const { users, isAdmin } = usersStore()
const { capture } = useTelemetry()

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
const DEFAULT_ROLE = 'Sales Executive'

const invitees = ref([])
const role = ref(DEFAULT_ROLE)
const error = ref(null)

const userExistMessage = computed(() => {
  const inviteesSet = new Set(invitees.value)
  if (!inviteesSet.size) return null

  if (!users.data?.crmUsers?.length) return null
  const existingEmails = users.data.crmUsers.map((user) => user.name)
  const existingUsersSet = new Set(existingEmails)

  const existingInvitees = inviteesSet.intersection(existingUsersSet)
  if (existingInvitees.size === 0) return null

  return __('User with email {0} already exists', [
    Array.from(existingInvitees).join(', '),
  ])
})

const inviteeExistMessage = computed(() => {
  const inviteesSet = new Set(invitees.value)
  if (!inviteesSet.size) return null

  if (!pendingInvitations.data?.length) return null
  const existingEmails = pendingInvitations.data.map((user) => user.email)
  const existingUsersSet = new Set(existingEmails)

  const existingInvitees = inviteesSet.intersection(existingUsersSet)
  if (existingInvitees.size === 0) return null

  return __('User with email {0} already invited', [
    Array.from(existingInvitees).join(', '),
  ])
})

const description = computed(() => {
  const descriptions = {
    'System Manager':
      'Can manage all aspects of the CRM, including user management, customizations and settings.',
    'Sales Head':
      'Full access across all areas, regions, and retail + projects pipelines. Can invite and manage users.',
    'Sales Coordinator':
      'Edit access across all leads (all types, all stages). Operational support.',
    RSM: 'Full access to Project leads in their assigned region and downstream team.',
    ASM: 'Full access to retail leads in their assigned area and downstream team.',
    'Sales Executive': 'Full access to retail leads assigned to them.',
    'Project Sales Executive': 'Full access to Project leads assigned to them.',
    Marketing:
      'Read access to all leads; write access to source/campaign fields.',
    'Calling Team': 'Create/edit leads up to C2; log calls; set dispositions.',
    'Jr. Sales Executive': 'Create/edit leads up to C2; view-only after C2.',
    'B2F Team':
      'Access only to leads at C7. Updates status, partner fabricator tag.',
    'Estimation Team': 'Access only to C2 leads. Uploads quote files.',
    Management: 'Read-only access across all pipelines and dashboards.',
  }
  return descriptions[role.value] || ''
})

const roleOptions = computed(() => {
  return [
    ...CRM_ROLES.map((r) => ({ value: r, label: __(r) })),
    ...(isAdmin() ? [{ value: 'System Manager', label: __('Admin') }] : []),
  ]
})

const roleMap = {
  'System Manager': __('Admin'),
  ...Object.fromEntries(CRM_ROLES.map((r) => [r, __(r)])),
}

const inviteByEmail = createResource({
  url: 'crm.api.invite_by_email',
  makeParams() {
    return {
      emails: convertArrayToString(invitees.value),
      role: role.value,
    }
  },
  onSuccess() {
    role.value = DEFAULT_ROLE
    error.value = null
    invitees.value = []
    pendingInvitations.reload()
    toast.success(__('Invitations sent successfully'))
    updateOnboardingStep('invite_your_team')
    capture('user_invited')
  },
  onError(err) {
    error.value = err?.messages?.[0]
    toast.error(error.value)
  },
})

const pendingInvitations = createListResource({
  type: 'list',
  doctype: 'CRM Invitation',
  filters: { status: 'Pending' },
  fields: ['name', 'email', 'role'],
  pageLength: 999,
  auto: true,
})

function updateInvitees(value) {
  const emails = value
    .split(',')
    .map((email) => email.trim())
    .filter((email) => validateEmail(email))
  invitees.value = emails
}
</script>
