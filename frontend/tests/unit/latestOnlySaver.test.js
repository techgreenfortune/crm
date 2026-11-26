import { createLatestOnlySaver } from '@/utils/latestOnlySaver'

// A send whose calls stay pending until the test settles them, in order.
function controlledSend() {
  const calls = []
  const send = vi.fn(
    (payload) =>
      new Promise((resolve, reject) => calls.push({ payload, resolve, reject })),
  )
  const settle = async (outcome = 'ok') => {
    const call = calls.shift()
    if (outcome === 'ok') call.resolve()
    else call.reject(new Error(outcome))
    await new Promise((r) => setTimeout(r, 0))
  }
  return { send, calls, settle }
}

describe('createLatestOnlySaver', () => {
  test('never has two saves in flight', async () => {
    const { send, calls, settle } = controlledSend()
    const save = createLatestOnlySaver({ send })

    save({ filters: 'a' })
    save({ filters: 'a+b' })

    expect(calls).toHaveLength(1)
    await settle()
    expect(calls).toHaveLength(1)
    expect(calls[0].payload).toEqual({ filters: 'a+b' })
  })

  test('changes made during a save collapse into only the latest one', async () => {
    const { send, settle } = controlledSend()
    const onSettled = vi.fn()
    const save = createLatestOnlySaver({ send, onSettled })

    save({ filters: 'a' })
    save({ filters: 'a+b' })
    save({ filters: 'a+b+c' })
    await settle()
    await settle()

    expect(send.mock.calls.map(([p]) => p.filters)).toEqual(['a', 'a+b+c'])
    expect(onSettled).toHaveBeenCalledTimes(1)
  })

  test('a failed save is retried before giving up', async () => {
    const { send, settle } = controlledSend()
    const onSettled = vi.fn()
    const onError = vi.fn()
    const save = createLatestOnlySaver({ send, onSettled, onError })

    save({ filters: 'a' })
    await settle('QueryDeadlockError')
    await settle()

    expect(send).toHaveBeenCalledTimes(2)
    expect(onSettled).toHaveBeenCalledTimes(1)
    expect(onError).not.toHaveBeenCalled()
  })

  test('a failure is reported when the retry also fails', async () => {
    const { send, settle } = controlledSend()
    const onSettled = vi.fn()
    const onError = vi.fn()
    const save = createLatestOnlySaver({ send, onSettled, onError })

    save({ filters: 'a' })
    await settle('first')
    await settle('second')

    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: 'second' }))
    expect(onSettled).not.toHaveBeenCalled()
  })

  test('a newer state queued during a failed save is sent instead of retrying the old one', async () => {
    const { send, settle } = controlledSend()
    const save = createLatestOnlySaver({ send })

    save({ filters: 'a' })
    save({ filters: 'a+b' })
    await settle('boom')
    await settle()

    expect(send.mock.calls.map(([p]) => p.filters)).toEqual(['a', 'a+b'])
  })

  test('saves again normally after the queue drains', async () => {
    const { send, settle } = controlledSend()
    const save = createLatestOnlySaver({ send })

    save({ filters: 'a' })
    await settle()
    save({ filters: 'b' })
    await settle()

    expect(send.mock.calls.map(([p]) => p.filters)).toEqual(['a', 'b'])
  })
})
