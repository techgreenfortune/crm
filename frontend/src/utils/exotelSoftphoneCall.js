export function extractExotelCallSid(payload) {
  if (!payload || typeof payload !== 'object') return ''
  for (const key of ['CallSid', 'callSid', 'Sid', 'sid']) {
    if (typeof payload[key] === 'string' && payload[key]) return payload[key]
  }
  for (const key of ['Data', 'data', 'Call', 'call']) {
    const callSid = extractExotelCallSid(payload[key])
    if (callSid) return callSid
  }
  return ''
}

// Agents opted in to the softphone never fall back to click-to-call; the per-agent toggle is the fallback.
export function chooseOutboundRoute({
  softphoneRequired,
  registered,
  registrationState,
}) {
  // null: config not loaded yet — don't guess, or a softphone agent could ring their mobile.
  if (softphoneRequired == null) return 'blocked-loading'
  if (!softphoneRequired) return 'click-to-call'
  if (registered) return 'softphone'
  if (registrationState === 'unsupported browser') return 'blocked-browser'
  if (registrationState === 'other tab') return 'blocked-other-tab'
  return 'blocked-reconnect'
}

// Header badge for the agent's browser phone. SDK states: registered, unregistered, sent_request;
// ExotelCallUI adds initializing, failed, unsupported browser and other tab.
export function softphoneStatusBadge(state) {
  if (state === 'registered') return { label: 'Phone ready', theme: 'green' }
  if (state === 'initializing' || state === 'sent_request')
    return { label: 'Phone connecting', theme: 'orange' }
  if (state === 'unsupported browser')
    return { label: 'Use Chrome for calls', theme: 'red' }
  if (state === 'other tab')
    return { label: 'Phone in another tab', theme: 'gray' }
  return { label: 'Phone offline', theme: 'red' }
}

// Popup label from an Integration Core terminal webhook for a browser outbound call. Only the
// webhook knows whether the customer answered: the SDK sees the agent leg connect either way.
export function softphoneTerminalLabel(data) {
  if (data?.Direction !== 'outbound-dial' || !data.CallLogStatus) return null
  if (data.CallLogStatus === 'Completed') return 'Call ended'
  if (data.CallLogStatus === 'Canceled') return 'Call canceled'
  if (['Call Not Answered', 'Busy', 'Failed'].includes(data.CallLogStatus))
    return 'No answer'
  return null
}

// The SDK retries a dropped WebSocket every 5 s by itself; give it this long before rebuilding.
export const RECONNECT_GRACE_MS = 15_000
export const RECONNECT_MAX_DELAY_MS = 5 * 60_000

export function createReconnectPolicy({ now = () => Date.now() } = {}) {
  let downSince = null
  let attempts = 0
  let nextAt = 0

  return {
    onState(state) {
      if (state === 'registered') {
        downSince = null
        attempts = 0
        nextAt = 0
      } else if (downSince === null) {
        downSince = now()
        nextAt = downSince + RECONNECT_GRACE_MS
      }
    },

    shouldReconnect({ inCall = false, online = true } = {}) {
      return downSince !== null && !inCall && online && now() >= nextAt
    },

    attempted() {
      attempts += 1
      nextAt =
        now() +
        Math.min(RECONNECT_GRACE_MS * 2 ** attempts, RECONNECT_MAX_DELAY_MS)
    },

    reconnectSoon() {
      if (downSince !== null) nextAt = now()
    },
  }
}

export const BUFFERED_INVITE_TTL_MS = 30_000
export const UNKNOWN_DIAL_GUARD_MS = 30_000

// Exotel rings the agent's browser (an "incoming" INVITE) as the first leg of an outbound call,
// and that INVITE can arrive before the dial request returns its CallSid. This decides what to
// do with each INVITE so the agent leg is auto-accepted only on an exact CallSid match.
export function createOutboundDialTracker({ now = () => Date.now() } = {}) {
  let state = 'idle'
  let callSid = ''
  let dialledAt = 0
  let accepted = false
  let buffered = null
  let rejectUntil = 0

  function takeBuffered() {
    const invite = buffered
    buffered = null
    if (!invite || now() - invite.at > BUFFERED_INVITE_TTL_MS) return null
    return invite
  }

  function clear() {
    state = 'idle'
    callSid = ''
    accepted = false
    buffered = null
  }

  return {
    get pending() {
      return state === 'pending'
    },

    start() {
      clear()
      state = 'pending'
    },

    onIncoming(inviteSid, details) {
      if (
        state === 'dialled' &&
        !accepted &&
        inviteSid !== callSid &&
        now() - dialledAt > BUFFERED_INVITE_TTL_MS
      ) {
        // Our agent leg never arrived; stop holding the line for it.
        clear()
      }
      if (state === 'dialled') {
        if (inviteSid !== callSid) return { action: 'reject' }
        if (accepted) return { action: 'ignore' }
        accepted = true
        return { action: 'accept' }
      }
      if (state === 'pending') {
        if (buffered?.callSid === inviteSid) return { action: 'ignore' }
        // One slot: the first INVITE is most likely our own agent leg.
        if (buffered) return { action: 'reject' }
        buffered = { callSid: inviteSid, details, at: now() }
        return { action: 'buffer' }
      }
      if (now() < rejectUntil) return { action: 'reject' }
      return { action: 'inbound' }
    },

    onCallEnded(endedSid) {
      if (buffered?.callSid === endedSid) buffered = null
    },

    dialSucceeded(dialledSid) {
      state = 'dialled'
      callSid = dialledSid
      dialledAt = now()
      const invite = takeBuffered()
      if (!invite) return { action: 'wait' }
      if (invite.callSid === dialledSid) {
        accepted = true
        return { action: 'accept' }
      }
      // The SDK holds one call at a time and this agent is starting one: they are busy.
      return { action: 'reject', callSid: invite.callSid }
    },

    dialFailed({ outcomeUnknown = false } = {}) {
      const invite = takeBuffered()
      clear()
      if (outcomeUnknown) {
        // Exotel may still ring our agent leg; without its CallSid it can't be told apart.
        rejectUntil = now() + UNKNOWN_DIAL_GUARD_MS
        return invite
          ? { action: 'reject', callSid: invite.callSid }
          : { action: 'none' }
      }
      // The dial definitely failed, so a buffered INVITE is a genuine inbound call.
      return invite
        ? {
            action: 'inbound',
            callSid: invite.callSid,
            details: invite.details,
          }
        : { action: 'none' }
    },

    reset() {
      clear()
    },
  }
}
