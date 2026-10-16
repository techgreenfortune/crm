const TECHNICAL_PATTERN =
  /^(no permission|not permitted|insufficient permission|traceback)|\b(doctype|frappe|CRM-[A-Z]+-)/i

export function getFriendlyDocError(err, label = 'record') {
  const type = err?.exc_type
  const first = err?.messages?.[0]

  if (type === 'PermissionError') {
    return {
      title: __('Access removed'),
      message: __(
        'You no longer have access to this {0}. It may have been reassigned or moved to another stage. Go back to the list to see your {0}s.',
        [label],
      ),
    }
  }

  if (type === 'DoesNotExistError') {
    return {
      title: __('{0} not found', [
        label.charAt(0).toUpperCase() + label.slice(1),
      ]),
      message: __('This {0} does not exist or has been deleted.', [label]),
    }
  }

  const safe = first && !TECHNICAL_PATTERN.test(first)
  return {
    title: __('Something went wrong'),
    message: safe
      ? first
      : __('We could not load this {0}. Please refresh and try again.', [
          label,
        ]),
  }
}
