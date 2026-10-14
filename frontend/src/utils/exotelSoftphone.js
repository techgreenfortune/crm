import {
  ExotelWebPhoneSDK,
  User,
  voipDomain,
  voipDomainSIP,
} from '@exotel-npm-dev/exotel-ip-calling-crm-websdk'
import { createMuteSync } from '@/utils/exotelSoftphoneCall'

let phone = null
let initializePromise = null
let registrationState = ''
// Bumped on every unregister; events from an older SDK instance are dropped.
let generation = 0
const callListeners = new Set()
const registrationListeners = new Set()

function emit(listeners, ...args) {
  for (const listener of listeners) listener(...args)
}

export function subscribeToExotelSoftphone({ onCallEvent, onRegistration }) {
  if (onCallEvent) callListeners.add(onCallEvent)
  if (onRegistration) {
    registrationListeners.add(onRegistration)
    if (registrationState) onRegistration(registrationState)
  }
  return () => {
    if (onCallEvent) callListeners.delete(onCallEvent)
    if (onRegistration) registrationListeners.delete(onRegistration)
  }
}

// Mirrors ExotelCRMWebSDK's private getSIPInfo so the app token can stay on the server.
// Re-check it on every SDK upgrade (docs/exotel-softphone.md, "Exotel SDK versions and upgrades").
// The doubled ":443" is what the SDK has always sent and is proven on real calls.
export function buildExotelSipInfo(user, exotelAccountSid) {
  const sipUser = user.sipId.split(':')[1]
  return {
    userName: sipUser,
    authUser: sipUser,
    sipdomain: `${exotelAccountSid}.${voipDomainSIP}`,
    domain: `${voipDomain}:443`,
    displayname: user.exotelUserName,
    secret: user.sipSecret,
    port: '443',
    security: 'wss',
    endpoint: 'wss',
  }
}

export async function initializeExotelSoftphone(mapping) {
  if (phone) return phone
  if (initializePromise) return initializePromise

  const instance = generation
  const current = () => instance === generation
  initializePromise = (async () => {
    const user = new User(mapping)
    const sipInfo = buildExotelSipInfo(user, mapping.ExotelAccountSid)
    if (!sipInfo.userName || !sipInfo.secret) {
      throw new Error('Exotel softphone SIP details are incomplete')
    }
    phone = new ExotelWebPhoneSDK(null, user).Initialize(
      sipInfo,
      (eventType, details) => {
        if (current()) emit(callListeners, eventType, details)
      },
      true,
      (state) => {
        if (!current()) return
        registrationState = state
        emit(registrationListeners, state)
      },
      () => {},
    )
    return phone
  })()

  try {
    return await initializePromise
  } catch (error) {
    initializePromise = null
    registrationState = ''
    throw error
  }
}

export function isExotelSoftphoneRegistered() {
  return registrationState === 'registered'
}

export function acceptExotelSoftphoneCall() {
  phone?.AcceptCall()
}

export function hangupExotelSoftphoneCall() {
  phone?.HangupCall()
}

const muteSync = createMuteSync(() => {
  if (!phone) return false
  phone.ToggleMute()
})

export function setExotelSoftphoneMute(muted) {
  muteSync.set(muted)
}

export function toggleExotelSoftphoneHold() {
  phone?.ToggleHold()
}

export function unregisterExotelSoftphone() {
  try {
    phone?.UnRegisterDevice()
  } finally {
    // Always drop the instance so a reconnect builds a fresh one, even if the SDK threw.
    generation += 1
    phone = null
    initializePromise = null
    registrationState = ''
  }
}
