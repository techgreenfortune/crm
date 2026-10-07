// Indian ring cadence: 400 Hz, 0.4 s on, 0.2 s off, 0.4 s on, 2 s off.
export const RING_PATTERN = [
  [0, 0.4],
  [0.6, 1.0],
]
export const RING_PERIOD_S = 3

export function createRingtone({
  createContext = () =>
    new (window.AudioContext || window.webkitAudioContext)(),
  frequency = 400,
  volume = 0.2,
} = {}) {
  let context = null
  let timer = null

  function ringOnce() {
    const start = context.currentTime
    for (const [on, off] of RING_PATTERN) {
      const oscillator = context.createOscillator()
      const gain = context.createGain()
      oscillator.frequency.value = frequency
      gain.gain.value = volume
      oscillator.connect(gain).connect(context.destination)
      oscillator.start(start + on)
      oscillator.stop(start + off)
    }
  }

  return {
    start() {
      if (timer) return
      try {
        context ||= createContext()
        // Browsers suspend audio until the page has had a click; the CRM tab normally has.
        context.resume?.()
        ringOnce()
        timer = setInterval(ringOnce, RING_PERIOD_S * 1000)
      } catch {
        timer = null
      }
    },
    stop() {
      clearInterval(timer)
      timer = null
      context?.close?.()
      context = null
    },
    get ringing() {
      return timer !== null
    },
  }
}
