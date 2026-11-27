// Adapted from upstream frappe/crm 1506ffaf8 (#2113). After our own save the views store
// is re-read, which trips the saved-view watcher into rebuilding the list from the store.
// That only replays state already applied locally, and when two re-reads overlap the older
// one can land last and rewind the filters. Count our own re-reads so the watcher skips them.
export function createSelfReloadGuard(reload) {
  let pending = 0

  return {
    reloadAfterSave() {
      pending++
      return Promise.resolve(reload()).catch((error) => {
        pending--
        throw error
      })
    },
    consumeSelfReload() {
      if (pending === 0) return false
      pending--
      return true
    },
  }
}
