<template>
  <div v-if="quote">
    <!-- Active QR -->
    <div
      class="activity flex cursor-pointer items-center gap-4 rounded p-2.5 duration-300 ease-in-out hover:bg-surface-gray-1"
      @click="openQuote(quote)"
    >
      <div class="flex flex-1 flex-col gap-1.5 text-base truncate">
        <div class="flex items-center gap-2">
          <span class="font-medium text-ink-gray-9 truncate">{{ quote.name }}</span>
          <Badge
            :label="__(quote.status)"
            :theme="statusTheme(quote.status)"
            variant="subtle"
            size="sm"
          />
        </div>
        <div class="flex flex-wrap gap-3 text-sm text-ink-gray-6">
          <span v-if="quote.quote_value">₹{{ formatCurrency(quote.quote_value) }}</span>
          <span v-if="quote.quote_margin">{{ quote.quote_margin }}% margin</span>
          <span v-if="quote.quote_sq_ft">{{ formatCurrency(quote.quote_sq_ft) }} sqft</span>
          <span v-if="quote.quote_number">{{ __('Ref:') }} {{ quote.quote_number }}</span>
          <span v-if="quote.requested_on">{{ formatDate(quote.requested_on, 'D MMM YYYY') }}</span>
        </div>
      </div>
      <div class="flex items-center gap-1">
        <Button
          v-if="quote.quote_file"
          :label="__('View File')"
          variant="subtle"
          size="sm"
          @click.stop="openFile(quote.quote_file)"
        />
        <Button
          :label="__('Open')"
          variant="subtle"
          size="sm"
          @click.stop="openQuote(quote)"
        />
      </div>
    </div>

    <!-- Revisions section -->
    <div v-if="quote.supersededQuotes?.length" class="ml-2 mt-1">
      <Button variant="ghost" size="sm" @click.stop="showRevisions = !showRevisions">
        <template #prefix>
          <FeatherIcon
            :name="showRevisions ? 'chevron-down' : 'chevron-right'"
            class="size-3"
          />
        </template>
        {{
          showRevisions
            ? __('Hide revisions')
            : __('View {0} revisions', [quote.supersededQuotes.length])
        }}
      </Button>

      <div
        v-if="showRevisions"
        class="mt-1.5 ml-3 flex flex-col gap-3 border-l border-outline-gray-2 pl-3"
      >
        <div
          v-for="(qr, idx) in quote.supersededQuotes"
          :key="qr.name"
          class="flex flex-col gap-1"
        >
          <div class="flex items-center gap-2 text-sm">
            <span class="font-medium text-ink-gray-7 shrink-0">
              {{ __('Round {0}', [idx + 1]) }}
            </span>
            <span v-if="qr.quote_value" class="text-ink-gray-6">
              ₹{{ formatCurrency(qr.quote_value) }}
            </span>
            <span v-if="qr.quote_margin" class="text-ink-gray-6">
              · {{ qr.quote_margin }}%
            </span>
            <Button
              v-if="qr.quote_file"
              :label="__('View')"
              variant="subtle"
              size="sm"
              @click.stop="openFile(qr.quote_file)"
            />
            <Button
              :label="__('Open')"
              variant="subtle"
              size="sm"
              @click.stop="openQuote(qr)"
            />
            <Tooltip :text="formatDate(qr.modified)">
              <span class="ml-auto text-xs text-ink-gray-5 shrink-0">
                {{ timeAgo(qr.modified) }}
              </span>
            </Tooltip>
          </div>
          <div v-if="qr.notes" class="pl-2 text-xs text-ink-gray-5 italic">
            {{ __('Revision: {0}', [qr.notes]) }}
          </div>
          <div v-if="qr.images?.length" class="pl-2 mt-0.5 flex flex-wrap gap-1.5">
            <img
              v-for="(img, imgIdx) in qr.images"
              :key="imgIdx"
              :src="img"
              class="size-12 rounded object-cover cursor-pointer border border-outline-gray-2"
              @click.stop="openFile(img)"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
<script setup>
import { computed, ref } from 'vue'
import { formatDate, timeAgo } from '@/utils'
import { useQuoteModal } from '@/composables/quoteModal'
import { Badge, Button, FeatherIcon, Tooltip } from 'frappe-ui'

const props = defineProps({
  quotes: { type: Array, default: () => [] },
  onReload: { type: Function, default: () => {} },
})

const { showQuoteModal } = useQuoteModal()

const quote = computed(() => props.quotes[0] || null)
const showRevisions = ref(false)

function openQuote(qr) {
  showQuoteModal({
    name: qr.name,
    onUpdated: () => props.onReload(),
  })
}

function openFile(url) {
  window.open(url, '_blank', 'noopener')
}

function formatCurrency(value) {
  return Number(value).toLocaleString('en-IN')
}

function statusTheme(status) {
  return (
    {
      Pending: 'orange',
      'Quote Received': 'blue',
      'Revision Requested': 'yellow',
      Accepted: 'green',
    }[status] || 'gray'
  )
}
</script>
