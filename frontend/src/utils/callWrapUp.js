// A call that has ended but whose disposition isn't saved yet is "in wrap-up". The popup holds
// one call, so a new call parks the wrap-up and the popup reopens it once that call is closed.

export function shouldParkWrapUp({ terminated, currentSid, nextSid }) {
  return Boolean(terminated && currentSid && currentSid !== nextSid)
}

export function parkWrapUp(queue, wrapUp) {
  const sid = wrapUp.callData.CallSid
  return [...queue.filter((w) => w.callData.CallSid !== sid), wrapUp]
}

export function isParkedCall(queue, callSid) {
  return Boolean(callSid) && queue.some((w) => w.callData.CallSid === callSid)
}
