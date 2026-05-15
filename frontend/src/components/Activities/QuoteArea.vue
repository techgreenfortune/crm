<template>
  <div v-if="quotes.length">
    <div v-for="(quote, i) in quotes" :key="quote.name">
      <div
        class="activity flex cursor-pointer items-center gap-4 rounded p-2.5 duration-300 ease-in-out hover:bg-surface-gray-1"
        @click="openQuote(quote)"
      >
        <div class="flex flex-1 flex-col gap-1.5 text-base truncate">
          <div class="flex items-center gap-2">
            <span class="font-medium text-ink-gray-9 truncate">{{
              quote.name
            }}</span>
            <Badge
              :label="__(quote.status)"
              :theme="statusTheme(quote.status)"
              variant="subtle"
              size="sm"
            />
          </div>
          <div class="flex flex-wrap gap-3 text-sm text-ink-gray-6">
            <span v-if="quote.quote_value">
              ₹{{ formatCurrency(quote.quote_value) }}
            </span>
            <span v-if="quote.quote_margin">
              {{ quote.quote_margin }}% margin
            </span>
            <span v-if="quote.requested_on">
              {{ formatDate(quote.requested_on, 'D MMM YYYY') }}
            </span>
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

      <div v-if="quote.revisions && quote.revisions.length > 1" class="ml-2 mt-1">
        <Button
          variant="ghost"
          size="sm"
          @click.stop="toggleExpand(quote.name)"
        >
          <template #prefix>
            <FeatherIcon
              :name="expanded[quote.name] ? 'chevron-down' : 'chevron-right'"
              class="size-3"
            />
          </template>
          {{
            expanded[quote.name]
              ? __('Hide revisions')
              : __('View {0} revisions', [quote.revisions.length])
          }}
        </Button>
        <div
          v-if="expanded[quote.name]"
          class="mt-1.5 ml-3 flex flex-col gap-1.5 border-l border-outline-gray-2 pl-3"
        >
          <div
            v-for="rev in quote.revisions"
            :key="rev.timestamp"
            class="flex items-center gap-2 text-sm"
          >
            <span class="font-medium text-ink-gray-7 shrink-0">
              {{ __('Round {0}', [rev.round]) }}
            </span>
            <span v-if="rev.value" class="text-ink-gray-6">
              ₹{{ formatCurrency(rev.value) }}
            </span>
            <span v-if="rev.margin" class="text-ink-gray-6">
              · {{ rev.margin }}%
            </span>
            <Button
              v-if="rev.file_url"
              :label="__('View')"
              variant="subtle"
              size="sm"
              @click.stop="openFile(rev.file_url)"
            />
            <Tooltip :text="formatDate(rev.timestamp)">
              <span class="ml-auto text-xs text-ink-gray-5">
                {{ timeAgo(rev.timestamp) }}
              </span>
            </Tooltip>
          </div>
        </div>
      </div>

      <div
        v-if="i < quotes.length - 1"
        class="mx-2 h-px border-t border-outline-gray-modals"
      />
    </div>
  </div>
</template>
<script setup>
import { ref } from 'vue'
import { formatDate, timeAgo } from '@/utils'
import { useDoctypeModal } from '@/composables/doctypeModal'
import { Badge, Button, FeatherIcon, Tooltip } from 'frappe-ui'

const props = defineProps({
  quotes: { type: Array, default: () => [] },
  onReload: { type: Function, default: () => {} },
})

const { showModal } = useDoctypeModal()

const expanded = ref({})

function toggleExpand(name) {
  expanded.value = { ...expanded.value, [name]: !expanded.value[name] }
}

function openQuote(quote) {
  showModal({
    name: quote.name,
    doctype: 'CRM Quote Request',
    title: __('Quote Request'),
    callbacks: {
      afterUpdate: () => props.onReload(),
    },
  })
}

function openFile(url) {
  window.open(url, '_blank')
}

function formatCurrency(value) {
  return Number(value).toLocaleString('en-IN')
}

function statusTheme(status) {
  const themes = {
    Pending: 'orange',
    'Quote Received': 'blue',
    'Revision Requested': 'yellow',
    Accepted: 'green',
  }
  return themes[status] || 'gray'
}
</script>
