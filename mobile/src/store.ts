/**
 * Persistent key/value storage for the mobile console.
 *
 * Strategy (honestly, no magic):
 *  - On a native platform (Android via Capacitor) we use @capacitor/preferences,
 *    which persists to the platform's SharedPreferences. That survives app
 *    restarts and is the supported Capacitor mechanism.
 *  - When the same bundle runs in a plain browser (e.g. `npx cap serve` or a
 *    developer preview where Capacitor.isNativePlatform() is false), the
 *    Preferences plugin has no native bridge to call. Rather than fail, we fall
 *    back to window.localStorage so the app remains usable for development.
 *    localStorage is NOT the production path on Android — it is only the
 *    documented web-preview fallback.
 *
 * All values are serialized as JSON strings. Callers get typed helpers.
 */

import { Capacitor } from '@capacitor/core'
import { Preferences } from '@capacitor/preferences'

const isNative = Capacitor.isNativePlatform()

/** Namespaced keys so we never collide with anything else in the WebView. */
export const StorageKeys = {
  gatewayUrl: 'moa.gateway.baseUrl',
  gatewayApiKey: 'moa.gateway.apiKey',
  lockEnabled: 'moa.security.lockEnabled',
  biometricEnabled: 'moa.security.biometricEnabled',
  pinSalt: 'moa.security.pinSalt',
  pinHash: 'moa.security.pinHash',
  failedAttempts: 'moa.security.failedAttempts',
  lockoutUntil: 'moa.security.lockoutUntil',
  lockOfferDecided: 'moa.security.lockOfferDecided',
  autoRefreshStatus: 'moa.ui.autoRefreshStatus'
} as const

function browserAvailable(): boolean {
  try {
    return typeof window !== 'undefined' && !!window.localStorage
  } catch {
    return false
  }
}

export async function setItem(key: string, value: string): Promise<void> {
  if (isNative) {
    await Preferences.set({ key, value })
    return
  }
  if (browserAvailable()) {
    window.localStorage.setItem(key, value)
  }
}

export async function getItem(key: string): Promise<string | null> {
  if (isNative) {
    const { value } = await Preferences.get({ key })
    return value
  }
  if (browserAvailable()) {
    return window.localStorage.getItem(key)
  }
  return null
}

export async function removeItem(key: string): Promise<void> {
  if (isNative) {
    await Preferences.remove({ key })
    return
  }
  if (browserAvailable()) {
    window.localStorage.removeItem(key)
  }
}

/** Convenience: read a JSON value, returning `fallback` on absence/parse error. */
export async function getJson<T>(key: string, fallback: T): Promise<T> {
  const raw = await getItem(key)
  if (raw === null || raw === undefined) return fallback
  try {
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

export async function setJson(key: string, value: unknown): Promise<void> {
  await setItem(key, JSON.stringify(value))
}

export async function getBool(key: string, fallback: boolean): Promise<boolean> {
  const raw = await getItem(key)
  if (raw === null || raw === undefined) return fallback
  return raw === 'true'
}

export async function setBool(key: string, value: boolean): Promise<void> {
  await setItem(key, value ? 'true' : 'false')
}
