const CHANNEL_NAME = 'crm-views'

// Saved views (incl. each user's standard-view filters) are one DB row shared by every
// tab and device. A tab holding an old copy would show it, and its next save would
// overwrite newer filters, so tabs re-fetch when another tab saves or when they regain focus.
export function createViewsSync({
  onRemoteChange,
  channel = typeof BroadcastChannel === 'undefined'
    ? null
    : new BroadcastChannel(CHANNEL_NAME),
  doc = globalThis.document,
}) {
  if (channel) channel.onmessage = () => onRemoteChange()

  const onVisibilityChange = () => {
    if (doc.visibilityState === 'visible') onRemoteChange()
  }
  doc?.addEventListener('visibilitychange', onVisibilityChange)

  return {
    notify: () => channel?.postMessage('changed'),
    stop: () => {
      channel?.close()
      doc?.removeEventListener('visibilitychange', onVisibilityChange)
    },
  }
}
