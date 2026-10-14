import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { buildExotelSipInfo } from '@/utils/exotelSoftphone'
import { extractExotelCallSid } from '@/utils/exotelSoftphoneCall'

const SDK = '@exotel-npm-dev/exotel-ip-calling-crm-websdk'
const require = createRequire(import.meta.url)

// The real entry point pulls in the WebRTC client, which only a bundler can load.
vi.mock('@exotel-npm-dev/exotel-ip-calling-crm-websdk', async () => ({
  ...(await import(
    '@exotel-npm-dev/exotel-ip-calling-crm-websdk/output/Constants.js'
  )),
  ExotelWebPhoneSDK: vi.fn(),
  User: vi.fn(),
}))

describe('extractExotelCallSid', () => {
  it('reads the dial response CallSid', () => {
    expect(
      extractExotelCallSid({ Status: 'Success', Data: { CallSid: 'abc-123' } }),
    ).toBe('abc-123')
  })

  it('reads SDK event callSid', () => {
    expect(extractExotelCallSid({ callSid: 'abc-123' })).toBe('abc-123')
  })

  it('reads classic Calls API Sid', () => {
    expect(extractExotelCallSid({ Call: { Sid: 'abc-123' } })).toBe('abc-123')
  })

  it('returns empty string for invalid payloads', () => {
    expect(extractExotelCallSid(null)).toBe('')
    expect(extractExotelCallSid({ Data: {} })).toBe('')
  })
})

describe('Exotel SDK surface the softphone relies on', () => {
  it('still exports the low-level classes', () => {
    const types = readFileSync(
      require.resolve(`${SDK}/output/index.d.ts`),
      'utf8',
    )
    expect(types).toContain(
      'export { default as ExotelWebPhoneSDK } from "./ExotelWebPhoneSDK"',
    )
    expect(types).toContain('export * from "./User"')
  })

  it('keeps the SIP endpoints the softphone registers against', () => {
    const constants = require(`${SDK}/output/Constants.js`)
    expect(constants.voipDomain).toBe('voip.in1.exotel.com:443')
    expect(constants.voipDomainSIP).toBe('voip.exotel.com')
  })

  it('builds the same SIP info as ExotelCRMWebSDK', () => {
    const user = {
      sipId: 'sip:agentsip',
      exotelUserName: 'Agent',
      sipSecret: 'secret',
    }
    expect(buildExotelSipInfo(user, 'acct')).toEqual({
      userName: 'agentsip',
      authUser: 'agentsip',
      sipdomain: 'acct.voip.exotel.com',
      domain: 'voip.in1.exotel.com:443:443',
      displayname: 'Agent',
      secret: 'secret',
      port: '443',
      security: 'wss',
      endpoint: 'wss',
    })
  })
})
