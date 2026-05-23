import { toast } from 'frappe-ui'
import { useDoctypeModal } from '@/composables/doctypeModal'

export function isQuoteTaskType(t) {
  return t === 'upload_quote' || t === 'review_quote'
}

export function quoteTaskTitle(t) {
  return t === 'upload_quote' ? __('Upload Quote') : __('Review Quote')
}

export function useQuoteRequestModal() {
  const { showModal } = useDoctypeModal()

  function openQuoteRequest(
    quoteRequestName,
    title = __('Quote Request'),
    callbacks = {},
  ) {
    if (!quoteRequestName) {
      toast.error(__('Task is not linked to a Quote Request.'))
      return
    }
    showModal({
      name: quoteRequestName,
      doctype: 'CRM Quote Request',
      customTitle: title,
      callbacks,
    })
  }

  return { openQuoteRequest, isQuoteTaskType, quoteTaskTitle }
}
