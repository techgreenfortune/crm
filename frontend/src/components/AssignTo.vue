<template>
  <Popover placement="bottom-end">
    <template #target="{ togglePopover }">
      <Tooltip :text="owner ? getUser(owner).full_name : __('Change Owner')">
        <div class="flex items-center" @click="togglePopover">
          <UserAvatar
            v-if="owner"
            :user="owner"
            size="md"
            class="cursor-pointer"
          />
          <Button v-else :label="__('Change Owner')" />
        </div>
      </Tooltip>
    </template>
    <template #body="{ isOpen }">
      <div
        v-show="isOpen"
        class="flex flex-col gap-2 my-2 w-[300px] rounded-lg bg-surface-modal shadow-2xl ring-1 ring-black p-3 ring-opacity-5 focus:outline-none"
      >
        <div class="text-base text-ink-gray-5">{{ __('Change Owner') }}</div>

        <!-- Current owner (single) — a lead/deal has exactly one. -->
        <div
          class="w-full min-h-9 flex flex-wrap items-center gap-1.5 p-1.5 rounded-lg bg-surface-gray-2"
        >
          <Tooltip v-if="owner" :text="owner">
            <div
              class="flex items-center text-sm p-0.5 text-ink-gray-6 border border-outline-gray-1 bg-surface-modal rounded-full"
            >
              <UserAvatar :user="owner" size="sm" />
              <div class="ml-1">{{ getUser(owner).full_name }}</div>
              <Button
                variant="ghost"
                class="rounded-full !size-4 m-1"
                @click.stop="setOwner('')"
              >
                <template #icon>
                  <FeatherIcon name="x" class="h-3 w-3 text-ink-gray-6" />
                </template>
              </Button>
            </div>
          </Tooltip>
          <div v-else class="text-sm text-ink-gray-4 px-1">
            {{ __('No owner') }}
          </div>
        </div>

        <!-- Picker — selecting a user REPLACES the owner (1-to-1). -->
        <Link
          ref="input"
          class="form-control"
          :value="''"
          doctype="User"
          :url="searchUrl"
          :hideMe="true"
          :placeholder="__('Set a new owner')"
          @change="(option) => setOwner(option)"
        >
          <template #item-prefix="{ option }">
            <UserAvatar class="mr-2" :user="option.value" size="sm" />
          </template>
          <template #item-label="{ option }">
            <Tooltip :text="option.value">
              <div class="cursor-pointer text-ink-gray-9">
                {{ getUser(option.value).full_name }}
              </div>
            </Tooltip>
          </template>
        </Link>

        <div class="flex items-center justify-between gap-2">
          <div
            class="text-base text-ink-gray-5 cursor-pointer select-none"
            @click="toggleMe"
          >
            {{ __('Assign to me') }}
          </div>
          <Switch :modelValue="ownedByMe" @click.stop="toggleMe" />
        </div>
      </div>
    </template>
  </Popover>
</template>

<script setup>
import UserAvatar from '@/components/UserAvatar.vue'
import Link from '@/components/Controls/Link.vue'
import { useDocument } from '@/data/document'
import { usersStore } from '@/stores/users'
import { Popover, Switch, Tooltip } from 'frappe-ui'
import { computed } from 'vue'

const props = defineProps({
  doctype: { type: String, default: '' },
  docname: { type: String, default: '' },
})

const { getUser } = usersStore()
const { document } = useDocument(props.doctype, props.docname)

// Ownership is a single 1-to-1 field — there is no multi-assignee. "Change
// Owner" edits this field exactly like the side-panel owner field.
const ownerField = computed(() =>
  props.doctype === 'CRM Lead'
    ? 'lead_owner'
    : props.doctype === 'CRM Deal'
      ? 'deal_owner'
      : null,
)

// Lead picker is hierarchy-scoped (ASM/RSM see only their subtree + upline);
// Deal picker has no tree rule. Both include the session user (claim-to-self).
const searchUrl =
  props.doctype === 'CRM Lead'
    ? 'crm.api.session.search_assignable_users'
    : 'crm.api.session.search_crm_users'

const owner = computed(() =>
  ownerField.value ? document.doc?.[ownerField.value] || '' : '',
)
const currentUser = computed(() => getUser('').name)
const ownedByMe = computed(() => owner.value === currentUser.value)

// Use setValue (frappe.client.set_value) instead of save so only the owner
// field is submitted — document.save would trigger full-document validation
// (mandatory fields unrelated to this change) and silently fail the update.
async function setOwner(newValue) {
  if (!ownerField.value || newValue === owner.value) return
  document.doc[ownerField.value] = newValue
  await document.setValue.submit({ [ownerField.value]: newValue })
}

function toggleMe() {
  setOwner(ownedByMe.value ? '' : currentUser.value)
}
</script>
