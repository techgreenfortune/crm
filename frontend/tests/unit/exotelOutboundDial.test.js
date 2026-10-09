import {
  BUFFERED_INVITE_TTL_MS,
  RECONNECT_GRACE_MS,
  RECONNECT_MAX_DELAY_MS,
  chooseOutboundRoute,
  createReconnectPolicy,
  softphoneStatusBadge,
  softphoneTerminalLabel,
  waitForCallOutcome,
  settleCallOutcome,
  CHECKING_CALL_RESULT,
  readSoftphoneSdkLog,
  UNKNOWN_DIAL_GUARD_MS,
  createOutboundDialTracker,
  createMuteSync,
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
  it('waits for the softphone config instead of guessing', () => {
    expect(
      chooseOutboundRoute({ softphoneRequired: null, registered: false }),
    ).toBe('blocked-loading')
    expect(
      chooseOutboundRoute({ softphoneRequired: undefined, registered: false }),
    ).toBe('blocked-loading')
  })

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

describe('softphoneTerminalLabel', () => {
  it('labels browser outbound calls from the terminal webhook', () => {
    const outbound = { Direction: 'outbound-dial' }
    expect(
      softphoneTerminalLabel({ ...outbound, CallLogStatus: 'Completed' }),
    ).toBe('Call ended')
    expect(
      softphoneTerminalLabel({ ...outbound, CallLogStatus: 'Canceled' }),
    ).toBe('Call canceled')
    for (const status of ['Call Not Answered', 'Busy', 'Failed'])
      expect(
        softphoneTerminalLabel({ ...outbound, CallLogStatus: status }),
      ).toBe('No answer')
  })

  it('ignores in-progress events, inbound calls and click-to-call payloads', () => {
    expect(
      softphoneTerminalLabel({
        Direction: 'outbound-dial',
        CallLogStatus: 'In Progress',
      }),
    ).toBeNull()
    expect(
      softphoneTerminalLabel({ Direction: 'incoming', CallLogStatus: 'Busy' }),
    ).toBeNull()
    expect(
      softphoneTerminalLabel({ Direction: 'outbound-dial', Status: 'busy' }),
    ).toBeNull()
  })
})

describe('createMuteSync', () => {
  it('toggles only when the requested state differs', () => {
    const toggle = vi.fn()
    const mute = createMuteSync(toggle)
    mute.set(false)
    expect(toggle).not.toHaveBeenCalled()
    mute.set(true)
    mute.set(true)
    expect(toggle).toHaveBeenCalledTimes(1)
  })

  it('realigns the SDK after a call that ended muted', () => {
    const toggle = vi.fn()
    const mute = createMuteSync(toggle)
    mute.set(true) // muted when the call ended
    mute.set(false) // next call connects
    expect(toggle).toHaveBeenCalledTimes(2)
    mute.set(true) // the agent's first mute really mutes
    expect(toggle).toHaveBeenCalledTimes(3)
  })

  it('keeps its state when there is no SDK to toggle', () => {
    const toggle = vi.fn().mockReturnValueOnce(false)
    const mute = createMuteSync(toggle)
    mute.set(true)
    mute.set(true)
    expect(toggle).toHaveBeenCalledTimes(2)
  })
})

describe('waitForCallOutcome', () => {
  const noSleep = () => Promise.resolve()

  it("waits for Exotel's verdict instead of trusting the agent leg", async () => {
    // Seen on prod: the agent leg connected, then the customer never answered.
    const statuses = ['In Progress', 'In Progress', 'Call Not Answered']
    const fetchStatus = vi.fn(() => Promise.resolve(statuses.shift()))

    await expect(
      waitForCallOutcome(fetchStatus, { sleep: noSleep }),
    ).resolves.toBe('No answer')
    expect(fetchStatus).toHaveBeenCalledTimes(3)
  })

  it('reports an answered call as ended', async () => {
    await expect(
      waitForCallOutcome(() => Promise.resolve('Completed'), {
        sleep: noSleep,
      }),
    ).resolves.toBe('Call ended')
  })

  it('gives up after the last attempt', async () => {
    const fetchStatus = vi.fn(() => Promise.resolve('In Progress'))

    await expect(
      waitForCallOutcome(fetchStatus, { attempts: 3, sleep: noSleep }),
    ).resolves.toBeNull()
    expect(fetchStatus).toHaveBeenCalledTimes(3)
  })

  it('keeps polling through a failed read', async () => {
    const fetchStatus = vi
      .fn()
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce('Completed')

    await expect(
      waitForCallOutcome(fetchStatus, { sleep: noSleep }),
    ).resolves.toBe('Call ended')
  })

  it('stops once something else settled the outcome', async () => {
    const fetchStatus = vi.fn()

    await expect(
      waitForCallOutcome(fetchStatus, { isSettled: () => true }),
    ).resolves.toBeNull()
    expect(fetchStatus).not.toHaveBeenCalled()
  })
})

describe('settleCallOutcome', () => {
  // A popup holding one call: what settleCallOutcome may read and change.
  function popup(sid = 'ours') {
    const state = { sid, status: CHECKING_CALL_RESULT, shown: [] }
    return {
      state,
      owns: (label) => state.sid === 'ours' && state.status === label,
      show: (label, { late }) => {
        state.status = label
        state.shown.push(late ? `late:${label}` : label)
      },
    }
  }
  const noSleep = () => Promise.resolve()
  const statuses = (...values) => {
    const queue = [...values]
    return vi.fn(() =>
      Promise.resolve(queue.length > 1 ? queue.shift() : queue[0]),
    )
  }

  it("shows Exotel's verdict once it lands", async () => {
    const p = popup()
    await settleCallOutcome({
      fetchStatus: statuses('In Progress', 'Call Not Answered'),
      ...p,
      fallback: 'Call ended',
      sleep: noSleep,
    })

    expect(p.state.shown).toEqual(['No answer'])
  })

  it('leaves the popup alone once a new call has taken it', async () => {
    const p = popup()
    const fetchStatus = vi.fn(async () => {
      p.state.sid = 'new-call'
      p.state.status = 'Calling...'
      return 'Call Not Answered'
    })
    await settleCallOutcome({
      fetchStatus,
      ...p,
      fallback: 'Call ended',
      sleep: noSleep,
    })

    expect(p.state.shown).toEqual([])
    expect(p.state.status).toBe('Calling...')
  })

  it('stops when a terminal webhook settled it first', async () => {
    const p = popup()
    const fetchStatus = vi.fn(async () => {
      p.state.status = 'Call ended'
      return 'In Progress'
    })
    await settleCallOutcome({
      fetchStatus,
      ...p,
      fallback: 'Call ended',
      sleep: noSleep,
    })

    expect(p.state.shown).toEqual([])
    expect(fetchStatus).toHaveBeenCalledTimes(1)
  })

  it('corrects a guess when a slow verdict disagrees', async () => {
    const p = popup()
    const fetchStatus = statuses(
      ...Array(10).fill('In Progress'),
      'Call Not Answered',
    )
    await settleCallOutcome({
      fetchStatus,
      ...p,
      fallback: 'Call ended',
      sleep: noSleep,
    })

    expect(p.state.shown).toEqual(['Call ended', 'late:No answer'])
  })

  it('keeps a guess that the slow verdict confirms', async () => {
    const p = popup()
    const fetchStatus = statuses(...Array(10).fill('In Progress'), 'Completed')
    await settleCallOutcome({
      fetchStatus,
      ...p,
      fallback: 'Call ended',
      sleep: noSleep,
    })

    expect(p.state.shown).toEqual(['Call ended'])
  })

  it('drops a slow verdict once the agent moved to another call', async () => {
    const p = popup()
    let reads = 0
    const fetchStatus = vi.fn(async () => {
      reads += 1
      if (reads === 11) p.state.sid = 'new-call'
      return reads > 11 ? 'Call Not Answered' : 'In Progress'
    })
    await settleCallOutcome({
      fetchStatus,
      ...p,
      fallback: 'Call ended',
      sleep: noSleep,
    })

    expect(p.state.shown).toEqual(['Call ended'])
  })
})

describe('awaitingAgentLeg', () => {
  it('waits for our agent leg after the dial reply', () => {
    const dial = createOutboundDialTracker()
    dial.start()
    dial.dialSucceeded('ours')

    expect(dial.awaitingAgentLeg('ours')).toBe(true)
    dial.onIncoming('ours', {})
    expect(dial.awaitingAgentLeg('ours')).toBe(false)
  })

  it('is not waiting when the ring came before the reply', () => {
    const dial = createOutboundDialTracker()
    dial.start()
    dial.onIncoming('ours', {})
    dial.dialSucceeded('ours')

    expect(dial.awaitingAgentLeg('ours')).toBe(false)
  })

  it('is not waiting for an older dial', () => {
    const dial = createOutboundDialTracker()
    dial.start()
    dial.dialSucceeded('ours')
    dial.reset()

    expect(dial.awaitingAgentLeg('ours')).toBe(false)
  })
})

describe('readSoftphoneSdkLog', () => {
  const storage = (value) => ({ getItem: () => value })

  it('returns the last lines the SDK stored', () => {
    const lines = Array.from({ length: 5 }, (_, i) => `line ${i}`)

    expect(
      readSoftphoneSdkLog({
        storage: storage(JSON.stringify(lines)),
        lines: 2,
      }),
    ).toBe('line 3\nline 4')
  })

  it('returns nothing when the SDK stored no log', () => {
    expect(readSoftphoneSdkLog({ storage: storage(null) })).toBe('')
    expect(readSoftphoneSdkLog({ storage: storage('not json') })).toBe('')
  })

  it('returns nothing when the browser blocks storage access', () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('SecurityError')
      },
    })
    try {
      expect(readSoftphoneSdkLog()).toBe('')
    } finally {
      Object.defineProperty(globalThis, 'localStorage', original)
    }
  })

  it('returns nothing when storage is blocked', () => {
    const blocked = {
      getItem: () => {
        throw new Error('blocked')
      },
    }
    expect(readSoftphoneSdkLog({ storage: blocked })).toBe('')
  })
})
