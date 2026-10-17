const TECHNICAL_PATTERN =
  /^(no permission|not permitted|not allowed|insufficient permission|traceback)|\b(doctype|frappe|CRM-[A-Z]+-)/i

export function getFriendlyDocError(err, doctype) {
  const type = err?.exc_type
  const first = err?.messages?.[0]
  const isLead = doctype === 'CRM Lead'

  if (type === 'PermissionError') {
    return {
      title: __("You don't have access"),
      message: isLead
        ? __(
            "You don't have access to this lead. It may have been reassigned or moved to another stage. Go back to the Leads list to see the leads available to you.",
          )
        : __("You don't have access to this record."),
    }
  }

  if (type === 'DoesNotExistError') {
    return {
      title: isLead ? __('Lead not found') : __('Record not found'),
      message: isLead
        ? __('This lead does not exist or has been deleted.')
        : __('This record does not exist or has been deleted.'),
    }
  }

  const safe = first && !TECHNICAL_PATTERN.test(first)
  return {
    title: __('Something went wrong'),
    message: safe
      ? first
      : isLead
        ? __('We could not load this lead. Please refresh and try again.')
        : __('We could not load this record. Please refresh and try again.'),
  }
}
