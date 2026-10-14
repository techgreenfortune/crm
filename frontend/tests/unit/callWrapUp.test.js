import { isParkedCall, parkWrapUp, shouldParkWrapUp } from '@/utils/callWrapUp'

const wrapUp = (sid, disposition = null) => ({
  callData: { CallSid: sid },
  disposition,
})

describe('shouldParkWrapUp', () => {
  it('parks an ended call when a different call takes the popup', () => {
    expect(
      shouldParkWrapUp({ terminated: true, currentSid: 'a', nextSid: 'b' }),
    ).toBe(true)
  })

  it('parks before an outgoing call whose sid is not known yet', () => {
    expect(
      shouldParkWrapUp({ terminated: true, currentSid: 'a', nextSid: null }),
    ).toBe(true)
  })

  it('keeps the popup for late events of the same call', () => {
    expect(
      shouldParkWrapUp({ terminated: true, currentSid: 'a', nextSid: 'a' }),
    ).toBe(false)
  })

  it('does not park a live call or an empty popup', () => {
    expect(
      shouldParkWrapUp({ terminated: false, currentSid: 'a', nextSid: 'b' }),
    ).toBe(false)
    expect(
      shouldParkWrapUp({ terminated: true, currentSid: '', nextSid: 'b' }),
    ).toBe(false)
  })
})

describe('parkWrapUp', () => {
  it('queues wrap-ups oldest first', () => {
    const queue = parkWrapUp(parkWrapUp([], wrapUp('a')), wrapUp('b'))
    expect(queue.map((w) => w.callData.CallSid)).toEqual(['a', 'b'])
  })

  it('replaces an earlier copy of the same call instead of duplicating it', () => {
    const queue = parkWrapUp(
      parkWrapUp([], wrapUp('a')),
      wrapUp('a', 'Interested'),
    )
    expect(queue).toEqual([wrapUp('a', 'Interested')])
  })
})

describe('isParkedCall', () => {
  it('finds parked calls by sid', () => {
    const queue = [wrapUp('a')]
    expect(isParkedCall(queue, 'a')).toBe(true)
    expect(isParkedCall(queue, 'b')).toBe(false)
    expect(isParkedCall(queue, undefined)).toBe(false)
  })
})
