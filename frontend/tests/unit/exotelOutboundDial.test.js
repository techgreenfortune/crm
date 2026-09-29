import {
  BUFFERED_INVITE_TTL_MS,
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
