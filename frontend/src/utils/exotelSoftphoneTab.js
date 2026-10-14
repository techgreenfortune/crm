// Only one tab per agent may register the SIP device: every registered tab rings on every call.
// Web Locks release automatically when the holding tab closes or crashes, and waiting tabs take
// over in order. Tabs in different browsers or on other machines can't be coordinated this way.
export function claimSoftphoneTab({
  user,
  steal = false,
  onAcquired,
  onLost,
  locks = globalThis.navigator?.locks,
}) {
  if (!locks) {
    onAcquired()
    return () => {}
  }

  let release
  const held = new Promise((resolve) => (release = resolve))
  let released = false

  locks
    .request(`crm-exotel-softphone:${user}`, { steal }, () => {
      if (released) return
      onAcquired()
      return held
    })
    .catch((error) => {
      // Another tab took the lock with steal: true.
      if (error?.name === 'AbortError' && !released) onLost()
    })

  return () => {
    released = true
    release()
  }
}
