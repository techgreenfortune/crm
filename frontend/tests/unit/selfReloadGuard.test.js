import { createSelfReloadGuard } from '@/utils/selfReloadGuard'

describe('createSelfReloadGuard', () => {
  test('each re-read after our own save is skipped exactly once', async () => {
    const guard = createSelfReloadGuard(() => Promise.resolve())

    await guard.reloadAfterSave()
    await guard.reloadAfterSave()

    expect(guard.consumeSelfReload()).toBe(true)
    expect(guard.consumeSelfReload()).toBe(true)
    expect(guard.consumeSelfReload()).toBe(false)
  })

  test('changes not caused by our own save are not skipped', () => {
    const guard = createSelfReloadGuard(() => Promise.resolve())

    expect(guard.consumeSelfReload()).toBe(false)
  })

  test('a failed re-read is not counted, so the next real change still applies', async () => {
    const guard = createSelfReloadGuard(() =>
      Promise.reject(new Error('offline')),
    )

    await expect(guard.reloadAfterSave()).rejects.toThrow('offline')

    expect(guard.consumeSelfReload()).toBe(false)
  })

  test('the reload runs every time', async () => {
    const reload = vi.fn(() => Promise.resolve())
    const guard = createSelfReloadGuard(reload)

    await guard.reloadAfterSave()
    await guard.reloadAfterSave()

    expect(reload).toHaveBeenCalledTimes(2)
  })
})
