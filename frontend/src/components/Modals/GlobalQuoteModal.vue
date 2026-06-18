<template>
  <QuoteModal
    v-if="qm.show.value"
    v-model="qm.show.value"
    :qr-name="qm.qrName.value"
    :lead-name="qm.leadName.value"
    :lead-doc="qm.leadDoc.value"
    @updated="onUpdated"
    @success="onSuccess"
  />
</template>
<script setup>
import QuoteModal from '@/components/Modals/QuoteModal.vue'
import { useQuoteModal } from '@/composables/quoteModal'

const qm = useQuoteModal()

function onUpdated() {
  qm.onUpdated.value?.()
}

function onSuccess(qrName) {
  qm.onUpdated.value?.(qrName)
}
</script>
