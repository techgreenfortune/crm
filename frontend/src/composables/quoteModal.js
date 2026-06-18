import { ref } from 'vue'

const show = ref(false)
const qrName = ref('')
const leadName = ref('')
const leadDoc = ref(null)
const onUpdated = ref(null)

function showQuoteModal({ name = '', leadName: _ln = '', leadDoc: _ld = null, onUpdated: _onUpdated = null } = {}) {
  qrName.value = name
  leadName.value = _ln
  leadDoc.value = _ld
  onUpdated.value = _onUpdated
  show.value = true
}

export function useQuoteModal() {
  return { show, qrName, leadName, leadDoc, onUpdated, showQuoteModal }
}
