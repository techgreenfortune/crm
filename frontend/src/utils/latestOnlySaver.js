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
