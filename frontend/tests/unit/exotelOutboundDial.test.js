import {
  BUFFERED_INVITE_TTL_MS,
  RECONNECT_GRACE_MS,
  RECONNECT_MAX_DELAY_MS,
  chooseOutboundRoute,
  createReconnectPolicy,
  softphoneStatusBadge,
  UNKNOWN_DIAL_GUARD_MS,
  createOutboundDialTracker,
} from '@/utils/exotelSoftphoneCall'

function tracker() {
  let time = 0
  const dial = createOutboundDialTracker({ now: () => time })
  return { dial, advance: (ms) => (time += ms) }
}

describe('createOutboundDialTracker', () => {
  it('accepts the agent leg when the dial response arrives first', () => {
    const { dial } = tracker()
    dial.start()
    expect(dial.dialSucceeded('sid-1')).toEqual({ action: 'wait' })
    expect(dial.onIncoming('sid-1')).toEqual({ action: 'accept' })
  })

  it('buffers the agent leg when the INVITE arrives first', () => {
    const { dial } = tracker()
    dial.start()
    expect(dial.onIncoming('sid-1', { callSid: 'sid-1' })).toEqual({
      action: 'buffer',
    })
    expect(dial.dialSucceeded('sid-1')).toEqual({ action: 'accept' })
  })

  it('rejects a different caller who rings while the agent is dialling', () => {
    const { dial } = tracker()
    dial.start()
    dial.onIncoming('other-sid')
    expect(dial.dialSucceeded('sid-1')).toEqual({
      action: 'reject',
      callSid: 'other-sid',
    })
  })

  it('rejects a different INVITE while the outbound call owns the SDK', () => {
    const { dial } = tracker()
    dial.start()
    dial.dialSucceeded('sid-1')
    expect(dial.onIncoming('other-sid')).toEqual({ action: 'reject' })
    expect(dial.onIncoming('sid-1')).toEqual({ action: 'accept' })
    expect(dial.onIncoming('other-sid-2')).toEqual({ action: 'reject' })
  })

  it('ignores a repeated INVITE for the call already accepted', () => {
    const { dial } = tracker()
    dial.start()
    dial.dialSucceeded('sid-1')
    dial.onIncoming('sid-1')
    expect(dial.onIncoming('sid-1')).toEqual({ action: 'ignore' })
  })

  it('stops holding the line when the agent leg never arrives', () => {
    const { dial, advance } = tracker()
    dial.start()
    dial.dialSucceeded('sid-1')
    advance(BUFFERED_INVITE_TTL_MS + 1)
    expect(dial.onIncoming('real-inbound')).toEqual({ action: 'inbound' })
  })

  it('ignores a duplicate INVITE and rejects a second one while pending', () => {
    const { dial } = tracker()
    dial.start()
    dial.onIncoming('sid-1')
    expect(dial.onIncoming('sid-1')).toEqual({ action: 'ignore' })
    expect(dial.onIncoming('sid-2')).toEqual({ action: 'reject' })
  })

  it('hands a buffered INVITE to the inbound path when the dial definitely failed', () => {
    const { dial } = tracker()
    dial.start()
    dial.onIncoming('inbound-sid', { callSid: 'inbound-sid' })
    expect(dial.dialFailed()).toEqual({
      action: 'inbound',
      callSid: 'inbound-sid',
      details: { callSid: 'inbound-sid' },
    })
  })

  it('rejects INVITEs for a while after a dial with an unknown outcome', () => {
    const { dial, advance } = tracker()
    dial.start()
    dial.onIncoming('late-sid')
    expect(dial.dialFailed({ outcomeUnknown: true })).toEqual({
      action: 'reject',
      callSid: 'late-sid',
    })
    expect(dial.onIncoming('late-sid-2')).toEqual({ action: 'reject' })
    advance(UNKNOWN_DIAL_GUARD_MS)
    expect(dial.onIncoming('real-inbound')).toEqual({ action: 'inbound' })
  })

  it('drops a buffered INVITE that expired or already ended', () => {
    const { dial, advance } = tracker()
    dial.start()
    dial.onIncoming('sid-1')
    advance(BUFFERED_INVITE_TTL_MS + 1)
    expect(dial.dialSucceeded('sid-1')).toEqual({ action: 'wait' })

    dial.start()
    dial.onIncoming('sid-2')
    dial.onCallEnded('sid-2')
    expect(dial.dialSucceeded('sid-2')).toEqual({ action: 'wait' })
  })

  it('treats INVITEs as inbound again after the call is reset', () => {
    const { dial } = tracker()
    dial.start()
    dial.dialSucceeded('sid-1')
    dial.reset()
    expect(dial.pending).toBe(false)
    expect(dial.onIncoming('sid-1')).toEqual({ action: 'inbound' })
  })
})

describe('chooseOutboundRoute', () => {
  it('keeps click-to-call for agents without the softphone', () => {
    expect(
      chooseOutboundRoute({ softphoneRequired: false, registered: false }),
    ).toBe('click-to-call')
  })

  it('uses the softphone when registered', () => {
    expect(
      chooseOutboundRoute({ softphoneRequired: true, registered: true }),
    ).toBe('softphone')
  })

  it('never falls back to the mobile for a softphone agent', () => {
    for (const registrationState of [
      'initializing',
      'failed',
      'unregistered',
      'terminated',
    ]) {
      expect(
        chooseOutboundRoute({
          softphoneRequired: true,
          registered: false,
          registrationState,
        }),
      ).toBe('blocked-reconnect')
    }
  })

  it('sends calls to the tab that holds the softphone', () => {
    expect(
      chooseOutboundRoute({
        softphoneRequired: true,
        registered: false,
        registrationState: 'other tab',
      }),
    ).toBe('blocked-other-tab')
  })

  it('blocks Safari without offering a reconnect', () => {
    expect(
      chooseOutboundRoute({
        softphoneRequired: true,
        registered: false,
        registrationState: 'unsupported browser',
      }),
    ).toBe('blocked-browser')
  })
})

describe('softphoneStatusBadge', () => {
  it('maps registration states to a header badge', () => {
    expect(softphoneStatusBadge('registered').theme).toBe('green')
    expect(softphoneStatusBadge('initializing').theme).toBe('orange')
    expect(softphoneStatusBadge('sent_request').theme).toBe('orange')
    expect(softphoneStatusBadge('other tab')).toEqual({
      label: 'Phone in another tab',
      theme: 'gray',
    })
    expect(softphoneStatusBadge('unsupported browser').label).toBe(
      'Use Chrome for calls',
    )
    for (const state of ['unregistered', 'failed', 'terminated', 'unknown'])
      expect(softphoneStatusBadge(state)).toEqual({
        label: 'Phone offline',
        theme: 'red',
      })
  })
})

describe('createReconnectPolicy', () => {
  function policy() {
    let time = 0
    const reconnect = createReconnectPolicy({ now: () => time })
    return { reconnect, advance: (ms) => (time += ms) }
  }

  it('leaves the SDK its own retry window before rebuilding', () => {
    const { reconnect, advance } = policy()
    reconnect.onState('unregistered')
    expect(reconnect.shouldReconnect()).toBe(false)
    advance(RECONNECT_GRACE_MS)
    expect(reconnect.shouldReconnect()).toBe(true)
  })

  it('backs off between attempts up to the maximum', () => {
    const { reconnect, advance } = policy()
    reconnect.onState('failed')
    advance(RECONNECT_GRACE_MS)
    reconnect.attempted()
    advance(RECONNECT_GRACE_MS * 2 - 1)
    expect(reconnect.shouldReconnect()).toBe(false)
    advance(1)
    expect(reconnect.shouldReconnect()).toBe(true)
    for (let i = 0; i < 10; i++) reconnect.attempted()
    advance(RECONNECT_MAX_DELAY_MS)
    expect(reconnect.shouldReconnect()).toBe(true)
  })

  it('never reconnects during a call or while offline', () => {
    const { reconnect, advance } = policy()
    reconnect.onState('unregistered')
    advance(RECONNECT_GRACE_MS)
    expect(reconnect.shouldReconnect({ inCall: true })).toBe(false)
    expect(reconnect.shouldReconnect({ online: false })).toBe(false)
  })

  it('reconnects straight away when the network comes back', () => {
    const { reconnect } = policy()
    reconnect.onState('unregistered')
    reconnect.reconnectSoon()
    expect(reconnect.shouldReconnect()).toBe(true)
  })

  it('resets once registered again', () => {
    const { reconnect, advance } = policy()
    reconnect.onState('unregistered')
    advance(RECONNECT_GRACE_MS)
    reconnect.onState('registered')
    expect(reconnect.shouldReconnect()).toBe(false)
    reconnect.reconnectSoon()
    expect(reconnect.shouldReconnect()).toBe(false)
  })
})
