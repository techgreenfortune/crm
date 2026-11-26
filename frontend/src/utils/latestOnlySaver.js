// Overlapping saves of the same record race on the server: one fails, and when it is the
// newer one the older state is what persists. So keep one save in flight and, when it
// settles, send only the latest state requested meanwhile.
export function createLatestOnlySaver({
  send,
  onSettled,
  onError,
  retries = 1,
}) {
  let running = null
  let queued = null

  async function flush(payload) {
    let current = payload
    let attempt = 0
    while (current) {
      try {
        await send(current)
        current = takeQueued()
        attempt = 0
      } catch (error) {
        const newer = takeQueued()
        if (newer) {
          current = newer
          attempt = 0
        } else if (attempt < retries) {
          attempt++
        } else {
          running = null
          onError?.(error)
          return
        }
      }
    }
    // Cleared before the callback so a save it triggers starts a new flush instead of
    // being queued behind one that is already finishing.
    running = null
    onSettled?.()
  }

  function takeQueued() {
    const next = queued
    queued = null
    return next
  }

  return function save(payload) {
    if (running) {
      queued = payload
      return running
    }
    running = flush(payload)
    return running
  }
}

const sharedSavers = new Map()

// List pages remount on every navigation, so a per-component queue would let a save
// still pending from before the remount race the new page's saves for the same row.
// One queue per key lives for the tab; only the currently mounted list handles results.
export function getSharedLatestOnlySaver(key, send) {
  if (!sharedSavers.has(key)) {
    let handlers = {}
    const save = createLatestOnlySaver({
      send,
      onSettled: () => handlers.onSettled?.(),
      onError: (error) => handlers.onError?.(error),
    })
    const bind = (next) => {
      handlers = next
      return () => {
        if (handlers === next) handlers = {}
      }
    }
    sharedSavers.set(key, { save, bind })
  }
  return sharedSavers.get(key)
}
