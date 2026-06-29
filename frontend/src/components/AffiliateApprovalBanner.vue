<template>
  <!-- Visible only when the lead is an affiliate lead.  Shows the current
       approval state + role-appropriate action buttons inline.

       State diagram on the lead:
         (empty)         → "Not yet submitted"          [Submit]
         Pending Approval → "Awaiting <sales_head>"     [Approve | Reject  (if viewer is Sales Head)]
         Approved         → "Approved by <user>"        ✓
         Rejected         → "Rejected by <user>: <remarks>" [Resubmit hint]
  -->
  <div
    v-if="isAffiliate"
    class="m-4 rounded-lg border p-4 text-sm"
    :class="bannerColor"
  >
    <div class="mb-2 flex items-center gap-2">
      <component :is="bannerIcon" class="size-5 shrink-0" />
      <div class="font-medium">{{ bannerTitle }}</div>
    </div>

    <div class="space-y-1 text-ink-gray-8">
      <div>
        <span class="text-ink-gray-6">{{ __('Affiliate') }}:</span>
        <b>{{ affiliateLabel }}</b>
      </div>
      <div>
        <span class="text-ink-gray-6">{{ __('Commission') }}:</span>
        <b>{{ commissionPct }}%</b>
      </div>
      <div v-if="lead.custom_affiliate_submitted_to">
        <span class="text-ink-gray-6">{{ __('Submitted to') }}:</span>
        <b>{{ userName(lead.custom_affiliate_submitted_to) }}</b>
        <span
          v-if="lead.custom_affiliate_submitted_at"
          class="text-ink-gray-5 ml-1"
        >
          ({{ formatDate(lead.custom_affiliate_submitted_at) }})
        </span>
      </div>
      <div v-if="lead.custom_affiliate_approved_by">
        <span class="text-ink-gray-6">{{ actionLabel }}:</span>
        <b>{{ userName(lead.custom_affiliate_approved_by) }}</b>
        <span
          v-if="lead.custom_affiliate_approved_at"
          class="text-ink-gray-5 ml-1"
        >
          ({{ formatDate(lead.custom_affiliate_approved_at) }})
        </span>
      </div>
      <div v-if="lead.custom_affiliate_approval_remarks">
        <span class="text-ink-gray-6">{{ __('Remarks') }}:</span>
        <span>{{ lead.custom_affiliate_approval_remarks }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import LucideAlertCircle from '~icons/lucide/alert-circle'
import LucideCheckCircle2 from '~icons/lucide/check-circle-2'
import LucideXCircle from '~icons/lucide/x-circle'
import LucideHourglass from '~icons/lucide/hourglass'
import { usersStore } from '@/stores/users'
import { formatDate } from '@/utils'

const props = defineProps({
  lead: { type: Object, required: true },
  affiliateName: { type: String, default: '' }, // resolved display name (optional)
})

const { getUser } = usersStore()

function userName(email) {
  if (!email) return ''
  const u = getUser(email)
  return u?.full_name || email
}

const isAffiliate = computed(() => !!props.lead?.custom_is_affiliate_lead)
const status = computed(
  () => props.lead?.custom_affiliate_approval_status || '',
)
const commissionPct = computed(
  () => props.lead?.custom_affiliate_commission_pct ?? 0,
)
const affiliateLabel = computed(
  () => props.affiliateName || props.lead?.custom_affiliate || '—',
)

const actionLabel = computed(() => {
  if (status.value === 'Approved') return __('Approved by')
  if (status.value === 'Rejected') return __('Rejected by')
  return __('Action by')
})

const bannerColor = computed(() => {
  switch (status.value) {
    case 'Approved':
      return 'border-green-200 bg-green-50 text-ink-gray-9'
    case 'Rejected':
      return 'border-red-200 bg-red-50 text-ink-gray-9'
    case 'Pending Approval':
      return 'border-amber-200 bg-amber-50 text-ink-gray-9'
    default:
      return 'border-outline-gray-2 bg-surface-gray-1 text-ink-gray-9'
  }
})

const bannerIcon = computed(() => {
  switch (status.value) {
    case 'Approved':
      return LucideCheckCircle2
    case 'Rejected':
      return LucideXCircle
    case 'Pending Approval':
      return LucideHourglass
    default:
      return LucideAlertCircle
  }
})

const bannerTitle = computed(() => {
  switch (status.value) {
    case 'Approved':
      return __('Affiliate commission approved')
    case 'Rejected':
      return __('Affiliate commission rejected — revise and resubmit')
    case 'Pending Approval':
      return __('Affiliate commission awaiting approval')
    default:
      return __('Affiliate commission not yet submitted for approval')
  }
})
</script>
