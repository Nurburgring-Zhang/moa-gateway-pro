/**
 * App-lock security: biometric unlock with PIN fallback.
 *
 * Flow (mirrors the gate logic required at app start):
 *   1. `checkBiometry()` tells us whether the device has enrolled biometry.
 *   2. If the user enabled biometric unlock and biometry is available, the
 *      native BiometricPrompt is shown via `authenticate()`.
 *   3. ANY failure — user cancel, fallback tapped, lockout, no hardware,
 *      web preview without a native bridge — degrades to the in-app PIN pad.
 *      The PIN is always the last resort and can never be bypassed.
 *
 * PIN storage: we never store the PIN. We store SHA-256(salt || pin) via the
 * Web Crypto API (available in the Capacitor WebView, which is a secure
 * context: https://localhost). A random 16-byte salt is generated per install.
 *
 * Throttling: after MAX_ATTEMPTS wrong PINs the pad locks for LOCKOUT_MS.
 * The counters persist, so killing the app does not reset them.
 */

import { Capacitor } from '@capacitor/core'
import {
  BiometricAuth,
  BiometryErrorType,
  type CheckBiometryResult
} from '@aparajita/capacitor-biometric-auth'

import { getBool, getItem, setBool, setItem, StorageKeys } from './store'

export const PIN_LENGTH = 6
const MAX_ATTEMPTS = 5
const LOCKOUT_MS = 30_000

export interface BiometricStatus {
  /** Device supports biometry AND the user enrolled at least one. */
  available: boolean
  /** Human-readable description of the biometry type / failure reason. */
  detail: string
  /** Whether the native plugin bridge exists at all. */
  nativePlugin: boolean
}

export interface UnlockAttempt {
  ok: boolean
  /** Remaining attempts before lockout (when ok === false). */
  remaining: number
  /** Epoch ms until which the pad is locked (0 = not locked). */
  lockoutUntil: number
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

/** SHA-256(salt || pin) as hex. Throws if Web Crypto is unavailable. */
export async function hashPin(pin: string, saltHex: string): Promise<string> {
  if (typeof crypto === 'undefined' || !crypto.subtle) {
    // Not a secure context — refuse instead of silently weakening security.
    throw new Error('当前环境不支持 Web Crypto，无法安全存储 PIN')
  }
  const saltBytes = new Uint8Array(
    saltHex.match(/.{2}/g)?.map((b) => parseInt(b, 16)) ?? []
  )
  const pinBytes = new TextEncoder().encode(pin)
  const material = new Uint8Array(saltBytes.length + pinBytes.length)
  material.set(saltBytes, 0)
  material.set(pinBytes, saltBytes.length)
  const digest = await crypto.subtle.digest('SHA-256', material.buffer as ArrayBuffer)
  return bytesToHex(new Uint8Array(digest))
}

export function generateSaltHex(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return bytesToHex(bytes)
}

// ==================== Biometry ====================

/**
 * Query biometry availability. On the web preview (no native bridge) the
 * plugin resolves with isAvailable=false, which is exactly the degrade-to-PIN
 * signal we want — so no special-casing is needed beyond catching hard errors.
 */
export async function getBiometricStatus(): Promise<BiometricStatus> {
  if (!Capacitor.isNativePlatform()) {
    return {
      available: false,
      detail: 'Web 预览环境无原生生物识别，仅可使用 PIN',
      nativePlugin: false
    }
  }
  try {
    const result: CheckBiometryResult = await BiometricAuth.checkBiometry()
    if (result.isAvailable) {
      return {
        available: true,
        detail: biometryLabel(result.biometryType),
        nativePlugin: true
      }
    }
    return {
      available: false,
      detail: result.reason || biometryUnavailableLabel(result.code),
      nativePlugin: true
    }
  } catch (err) {
    return {
      available: false,
      detail: `生物识别检测失败：${err instanceof Error ? err.message : String(err)}`,
      nativePlugin: true
    }
  }
}

function biometryLabel(type: number): string {
  switch (type) {
    case 3:
      return '指纹识别'
    case 4:
      return '人脸识别'
    case 5:
      return '虹膜识别'
    case 1:
      return 'Touch ID'
    case 2:
      return 'Face ID'
    default:
      return '生物识别'
  }
}

function biometryUnavailableLabel(code: BiometryErrorType | string): string {
  switch (code) {
    case BiometryErrorType.biometryNotEnrolled:
      return '设备未录入生物识别信息'
    case BiometryErrorType.biometryLockout:
      return '生物识别已被系统临时锁定'
    case BiometryErrorType.passcodeNotSet:
      return '设备未设置锁屏密码'
    case BiometryErrorType.noDeviceCredential:
      return '设备未设置任何解锁凭据'
    default:
      return '设备不支持生物识别'
  }
}

/**
 * Show the native biometric prompt.
 * Resolves true on success; false on any failure (with the reason via
 * `onFailure` so the UI can explain the degrade to PIN).
 */
export async function authenticateBiometric(
  onFailure: (reason: string) => void
): Promise<boolean> {
  try {
    await BiometricAuth.authenticate({
      reason: '解锁 MOA Gateway 控制台',
      androidTitle: 'MOA Gateway',
      androidSubtitle: '验证身份以打开控制台',
      cancelTitle: '使用 PIN',
      // Keep device-credential fallback inside the native dialog off: our own
      // PIN pad is the explicit, audited fallback path.
      allowDeviceCredential: false,
      androidConfirmationRequired: true
    })
    return true
  } catch (err) {
    const code =
      err && typeof err === 'object' && 'code' in err
        ? String((err as { code: unknown }).code)
        : ''
    if (code === BiometryErrorType.userCancel || code === BiometryErrorType.appCancel) {
      onFailure('已取消生物识别，请使用 PIN 解锁')
    } else if (code === BiometryErrorType.userFallback) {
      onFailure('已选择备用方式，请使用 PIN 解锁')
    } else if (code === BiometryErrorType.biometryLockout) {
      onFailure('生物识别尝试次数过多已被锁定，请使用 PIN 解锁')
    } else {
      onFailure(
        `生物识别失败（${code || 'unknown'}）：${
          err instanceof Error ? err.message : String(err)
        }`
      )
    }
    return false
  }
}

// ==================== PIN persistence ====================

export async function isPinSet(): Promise<boolean> {
  const hash = await getItem(StorageKeys.pinHash)
  return !!hash
}

export async function savePin(pin: string): Promise<void> {
  const salt = generateSaltHex()
  const hash = await hashPin(pin, salt)
  await setItem(StorageKeys.pinSalt, salt)
  await setItem(StorageKeys.pinHash, hash)
  // New PIN resets the throttle counters.
  await setItem(StorageKeys.failedAttempts, '0')
  await setItem(StorageKeys.lockoutUntil, '0')
}

export async function clearPin(): Promise<void> {
  await setItem(StorageKeys.pinHash, '')
  await setItem(StorageKeys.pinSalt, '')
  await setItem(StorageKeys.failedAttempts, '0')
  await setItem(StorageKeys.lockoutUntil, '0')
}

export async function isLockEnabled(): Promise<boolean> {
  return getBool(StorageKeys.lockEnabled, false)
}

export async function setLockEnabled(enabled: boolean): Promise<void> {
  await setBool(StorageKeys.lockEnabled, enabled)
}

export async function isBiometricUnlockEnabled(): Promise<boolean> {
  return getBool(StorageKeys.biometricEnabled, true)
}

export async function setBiometricUnlockEnabled(enabled: boolean): Promise<void> {
  await setBool(StorageKeys.biometricEnabled, enabled)
}

/** Current lockout state: epoch ms until which PIN entry is blocked. */
export async function getLockoutUntil(): Promise<number> {
  const raw = await getItem(StorageKeys.lockoutUntil)
  const v = raw ? parseInt(raw, 10) : 0
  if (Number.isNaN(v)) return 0
  return v > Date.now() ? v : 0
}

/** Verify a PIN attempt; maintains the failure counter + lockout window. */
export async function verifyPin(pin: string): Promise<UnlockAttempt> {
  const lockoutUntil = await getLockoutUntil()
  if (lockoutUntil > 0) {
    return { ok: false, remaining: 0, lockoutUntil }
  }
  const salt = (await getItem(StorageKeys.pinSalt)) ?? ''
  const expected = await getItem(StorageKeys.pinHash)
  if (!expected) {
    // No PIN configured — nothing to verify against. Treat as failure; the UI
    // should have routed to PIN setup instead.
    return { ok: false, remaining: 0, lockoutUntil: 0 }
  }
  const actual = await hashPin(pin, salt)
  if (actual === expected) {
    await setItem(StorageKeys.failedAttempts, '0')
    await setItem(StorageKeys.lockoutUntil, '0')
    return { ok: true, remaining: MAX_ATTEMPTS, lockoutUntil: 0 }
  }
  const rawAttempts = await getItem(StorageKeys.failedAttempts)
  const attempts = (rawAttempts ? parseInt(rawAttempts, 10) : 0) + 1
  if (attempts >= MAX_ATTEMPTS) {
    const until = Date.now() + LOCKOUT_MS
    await setItem(StorageKeys.failedAttempts, '0')
    await setItem(StorageKeys.lockoutUntil, String(until))
    return { ok: false, remaining: 0, lockoutUntil: until }
  }
  await setItem(StorageKeys.failedAttempts, String(attempts))
  return { ok: false, remaining: MAX_ATTEMPTS - attempts, lockoutUntil: 0 }
}
