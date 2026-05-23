import { call, toast } from 'frappe-ui'
import { useDoctypeModal } from '@/composables/doctypeModal'

export function isQuoteTaskType(t) {
  return t === 'upload_quote' || t === 'review_quote'
}

export function quoteTaskTitle(t) {
  return t === 'upload_quote' ? __('Upload Quote') : __('Review Quote')
}

export function useQuoteRequestModal() {
  const { showModal } = useDoctypeModal()

  async function openQuoteRequest(
    leadName,
    title = __('Quote Request'),
    callbacks = {},
  ) {
    if (!leadName) {
      toast.error(__('Task is not linked to a lead.'))
      return
    }
    try {
      const rows = await call('frappe.client.get_list', {
        doctype: 'CRM Quote Request',
        filters: { lead: leadName },
        fields: ['name'],
        order_by: 'creation desc',
        limit: 1,
      })
      if (!rows?.length) {
        toast.error(__('No Quote Request found for this lead.'))
        return
      }
      showModal({
        name: rows[0].name,
        doctype: 'CRM Quote Request',
        customTitle: title,
        callbacks,
      })
    } catch (err) {
      toast.error(err?.message || __('Could not open Quote Request.'))
    }
  }

  return { openQuoteRequest, isQuoteTaskType, quoteTaskTitle }
}
