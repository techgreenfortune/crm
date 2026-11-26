import { createViewsSync } from '@/utils/viewsSync'

// BroadcastChannel delivers to every other channel of the same name, never the sender.
function fakeChannelBus() {
  const channels = []
  return () => {
    const channel = {
      onmessage: null,
      closed: false,
      postMessage(data) {
        for (const other of channels) {
          if (other !== channel && !other.closed) other.onmessage?.({ data })
        }
      },
      close() {
        channel.closed = true
      },
    }
    channels.push(channel)
    return channel
  }
}

function fakeDocument() {
  const listeners = new Set()
  return {
    visibilityState: 'visible',
    addEventListener: (_, fn) => listeners.add(fn),
    removeEventListener: (_, fn) => listeners.delete(fn),
    setVisibility(state) {
      this.visibilityState = state
      listeners.forEach((fn) => fn())
    },
  }
}

describe('createViewsSync', () => {
  test('a save in one tab refreshes the other tabs, not itself', () => {
    const newChannel = fakeChannelBus()
    const tabA = vi.fn()
    const tabB = vi.fn()
    const a = createViewsSync({ onRemoteChange: tabA, channel: newChannel(), doc: fakeDocument() })
    createViewsSync({ onRemoteChange: tabB, channel: newChannel(), doc: fakeDocument() })

    a.notify()

    expect(tabA).not.toHaveBeenCalled()
    expect(tabB).toHaveBeenCalledTimes(1)
  })

  test('a tab refreshes when it becomes visible again', () => {
    const doc = fakeDocument()
    const onRemoteChange = vi.fn()
    createViewsSync({ onRemoteChange, channel: null, doc })

    doc.setVisibility('hidden')
    expect(onRemoteChange).not.toHaveBeenCalled()

    doc.setVisibility('visible')
    expect(onRemoteChange).toHaveBeenCalledTimes(1)
  })

  test('stop detaches from both the channel and visibility changes', () => {
    const newChannel = fakeChannelBus()
    const doc = fakeDocument()
    const onRemoteChange = vi.fn()
    const sync = createViewsSync({ onRemoteChange, channel: newChannel(), doc })
    const other = createViewsSync({ onRemoteChange: vi.fn(), channel: newChannel(), doc: fakeDocument() })

    sync.stop()
    other.notify()
    doc.setVisibility('visible')

    expect(onRemoteChange).not.toHaveBeenCalled()
  })

  test('works without BroadcastChannel support', () => {
    const sync = createViewsSync({ onRemoteChange: vi.fn(), channel: null, doc: fakeDocument() })
    expect(() => sync.notify()).not.toThrow()
  })
})
