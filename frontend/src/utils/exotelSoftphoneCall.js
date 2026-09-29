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
        return invite ? { action: 'reject', callSid: invite.callSid } : { action: 'none' }
      }
      // The dial definitely failed, so a buffered INVITE is a genuine inbound call.
      return invite
        ? { action: 'inbound', callSid: invite.callSid, details: invite.details }
        : { action: 'none' }
    },

    reset() {
      clear()
    },
  }
}
