import { createRingtone, RING_PATTERN, RING_PERIOD_S } from '@/utils/ringtone'

function fakeContext() {
  const oscillators = []
  const node = () => ({ connect: (next) => next })
  return {
    oscillators,
    currentTime: 0,
    destination: {},
    resume: vi.fn(),
    close: vi.fn(),
    createGain: () => ({ ...node(), gain: { value: 0 } }),
    createOscillator: () => {
      const oscillator = {
        ...node(),
        frequency: { value: 0 },
        start: vi.fn(),
        stop: vi.fn(),
      }
      oscillators.push(oscillator)
      return oscillator
    },
  }
}

describe('createRingtone', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('rings the cadence immediately and repeats it until stopped', () => {
    const context = fakeContext()
    const ringtone = createRingtone({ createContext: () => context })

    ringtone.start()
    expect(context.oscillators).toHaveLength(RING_PATTERN.length)
    vi.advanceTimersByTime(RING_PERIOD_S * 1000)
    expect(context.oscillators).toHaveLength(RING_PATTERN.length * 2)

    ringtone.stop()
    vi.advanceTimersByTime(RING_PERIOD_S * 3000)
    expect(context.oscillators).toHaveLength(RING_PATTERN.length * 2)
    expect(context.close).toHaveBeenCalled()
    expect(ringtone.ringing).toBe(false)
  })

  it('ignores a second start while ringing', () => {
    const context = fakeContext()
    const ringtone = createRingtone({ createContext: () => context })
    ringtone.start()
    ringtone.start()
    expect(context.oscillators).toHaveLength(RING_PATTERN.length)
    ringtone.stop()
  })

  it('stays silent instead of throwing when audio is unavailable', () => {
    const ringtone = createRingtone({
      createContext: () => {
        throw new Error('no audio')
      },
    })
    expect(() => ringtone.start()).not.toThrow()
    expect(ringtone.ringing).toBe(false)
  })
})
