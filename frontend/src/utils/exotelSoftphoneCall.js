export function extractExotelCallSid(payload) {
  if (!payload || typeof payload !== 'object') return ''
  for (const key of ['CallSid', 'callSid', 'Sid', 'sid']) {
    if (typeof payload[key] === 'string' && payload[key]) return payload[key]
  }
  for (const key of ['Data', 'data', 'Call', 'call']) {
    const callSid = extractExotelCallSid(payload[key])
    if (callSid) return callSid
  }
  return ''
}
