import { toast } from 'frappe-ui'
import { useQuoteModal } from '@/composables/quoteModal'

export function isQuoteTaskType(t) {
  return t === 'upload_quote' || t === 'review_quote'
}

export function quoteTaskTitle(t) {
  return t === 'upload_quote' ? __('Upload Quote') : __('Review Quote')
}

export function useQuoteRequestModal() {
  const { showQuoteModal } = useQuoteModal()

  function openQuoteRequest(quoteRequestName, _title, callbacks = {}) {
    if (!quoteRequestName) {
      toast.error(__('Task is not linked to a Quote Request.'))
      return
    }
    showQuoteModal({
      name: quoteRequestName,
      onUpdated: callbacks?.afterUpdate || null,
    })
  }

  return { openQuoteRequest, isQuoteTaskType, quoteTaskTitle }
}
