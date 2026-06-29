<template>
  <Dialog v-model="show" :options="{ size: 'lg', title: __('Submit for Approval') }">
    <template #body-content>
      <div class="space-y-4">
        <div class="rounded-lg border border-outline-gray-2 bg-surface-gray-1 p-3 text-sm">
          <div class="text-ink-gray-7 mb-1">{{ __('You are about to submit:') }}</div>
          <ul class="space-y-1 text-ink-gray-9">
            <li>
              <span class="text-ink-gray-6">{{ __('Affiliate') }}:</span>
              <b>{{ summary.affiliate_name || summary.affiliate }}</b>
            </li>
            <li>
              <span class="text-ink-gray-6">{{ __('Commission') }}:</span>
              <b>{{ summary.commission_pct }}%</b>
            </li>
          </ul>
        </div>

        <div>
          <label class="mb-1 block text-sm font-medium text-ink-gray-7">
            {{ __('Send approval request to') }}
          </label>
          <div
            v-if="salesHeads.loading"
            class="text-sm text-ink-gray-5"
          >
            {{ __('Loading sales heads…') }}
          </div>
          <div
            v-else-if="!salesHeads.data?.length"
            class="text-sm text-ink-red-5"
          >
            {{ __('No Sales Head configured. Assign the Sales Head role to a user first.') }}
          </div>
          <div v-else class="max-h-60 overflow-y-auto rounded-lg border border-outline-gray-2">
            <button
              v-for="u in salesHeads.data"
              :key="u.name"
              type="button"
              class="flex w-full items-center gap-2 border-b border-outline-gray-1 px-3 py-2 text-sm last:border-b-0 hover:bg-surface-gray-2"
              :class="selected === u.name && 'bg-surface-gray-2'"
              @click="selected = u.name"
            >
              <LucideCheck
                v-if="selected === u.name"
                class="size-4 text-ink-gray-8"
              />
              <span v-else class="size-4" />
              <UserAvatar :user="u.name" size="sm" />
              <div class="text-left">
                <div>{{ u.full_name || u.name }}</div>
                <div class="text-xs text-ink-gray-5">{{ u.name }}</div>
              </div>
            </button>
          </div>
        </div>

        <ErrorMessage v-if="error" :message="error" />
      </div>
    </template>
    <template #actions>
      <Button
        variant="solid"
        :loading="submitting"
        :disabled="!selected || !salesHeads.data?.length"
        @click="submit"
      >
        {{ __('Submit for Approval') }}
      </Button>
      <Button variant="outline" @click="show = false">
        {{ __('Cancel') }}
      </Button>
    </template>
  </Dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Dialog, Button, ErrorMessage, call, createResource, toast } from 'frappe-ui'
import LucideCheck from '~icons/lucide/check'
import UserAvatar from '@/components/UserAvatar.vue'

const props = defineProps({
  leadId: { type: String, required: true },
  summary: {
    type: Object,
    default: () => ({ affiliate: '', affiliate_name: '', commission_pct: 0 }),
  },
})
const emit = defineEmits(['submitted'])

const show = defineModel({ type: Boolean })
const selected = ref(null)
const error = ref(null)
const submitting = ref(false)

const salesHeads = createResource({
  url: 'crm.api.affiliate.get_sales_heads',
  auto: true,
})

// Reset selection each time the modal re-opens (after first mount)
watch(show, (open) => {
  if (open) {
    selected.value = null
    error.value = null
    submitting.value = false
    salesHeads.reload()
  }
})

async function submit() {
  if (!selected.value) return
  submitting.value = true
  error.value = null
  try {
    await call('crm.api.affiliate.submit_for_approval', {
      lead_name: props.leadId,
      sales_head: selected.value,
    })
    toast.success(__('Submitted for approval'))
    emit('submitted', { sales_head: selected.value })
    show.value = false
  } catch (err) {
    // Frappe-UI exposes server-thrown messages on `err.messages`; some shapes
    // also use `err.exc` / `err.message`.  Try them in order before falling
    // back to the raw stringification so the user always sees a real reason.
    const msg =
      err?.messages?.[0] ||
      err?.message ||
      err?.error?.messages?.[0] ||
      String(err)
    error.value = msg
    toast.error(msg)
  } finally {
    submitting.value = false
  }
}
</script>
