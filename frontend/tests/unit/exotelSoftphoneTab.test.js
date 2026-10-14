import { claimSoftphoneTab } from '@/utils/exotelSoftphoneTab'

// Minimal Web Locks stand-in: one holder per name, a FIFO queue, and steal.
function fakeLocks() {
  const state = new Map()
  function grantNext(name) {
    const entry = state.get(name)
    const next = entry.queue.shift()
    if (!next) {
      entry.holder = null
      return
    }
    entry.holder = next
    Promise.resolve(next.callback()).then(() => {
      if (entry.holder === next) grantNext(name)
      next.resolve()
    })
  }
  return {
    request(name, options, callback) {
      if (!state.has(name)) state.set(name, { holder: null, queue: [] })
      const entry = state.get(name)
      return new Promise((resolve, reject) => {
        const request = { callback, resolve, reject }
        if (options.steal && entry.holder) {
          const previous = entry.holder
          entry.holder = null
          const error = new Error('stolen')
          error.name = 'AbortError'
          previous.reject(error)
          entry.queue.unshift(request)
        } else {
          entry.queue.push(request)
        }
        if (!entry.holder) grantNext(name)
      })
    },
  }
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

function tab(locks, name, events, steal = false) {
  return claimSoftphoneTab({
    user: 'agent@example.com',
    steal,
    locks,
    onAcquired: () => events.push(`${name}:acquired`),
    onLost: () => events.push(`${name}:lost`),
  })
}

describe('claimSoftphoneTab', () => {
  it('lets only the first tab register; the next takes over when it closes', async () => {
    const locks = fakeLocks()
    const events = []
    const releaseA = tab(locks, 'A', events)
    tab(locks, 'B', events)
    await flush()
    expect(events).toEqual(['A:acquired'])

    releaseA()
    await flush()
    expect(events).toEqual(['A:acquired', 'B:acquired'])
  })

  it('"Use this tab" takes the lock and tells the old tab', async () => {
    const locks = fakeLocks()
    const events = []
    tab(locks, 'A', events)
    await flush()
    tab(locks, 'B', events, true)
    await flush()
    // The two tabs run independently, so "lost" and "acquired" may arrive in either order.
    expect(events[0]).toBe('A:acquired')
    expect(events.slice(1).sort()).toEqual(['A:lost', 'B:acquired'])
  })

  it('a tab closed while waiting never registers', async () => {
    const locks = fakeLocks()
    const events = []
    const releaseA = tab(locks, 'A', events)
    const releaseB = tab(locks, 'B', events)
    await flush()
    releaseB()
    releaseA()
    await flush()
    expect(events).toEqual(['A:acquired'])
  })

  it('registers straight away where Web Locks are unavailable', () => {
    const events = []
    claimSoftphoneTab({
      user: 'agent@example.com',
      locks: null,
      onAcquired: () => events.push('acquired'),
      onLost: () => events.push('lost'),
    })
    expect(events).toEqual(['acquired'])
  })
})
