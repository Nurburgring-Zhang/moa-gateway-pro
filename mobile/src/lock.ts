/**
 * App-start lock gate.
 *
 * Sequence:
 *   1. If the app lock is disabled → resolve immediately.
 *   2. If biometric unlock is enabled AND biometry is available → present the
 *      native BiometricPrompt automatically.
 *   3. On ANY biometric failure (cancel, lockout, hardware, bridge missing)
 *      the PIN pad is shown — the documented, always-available fallback.
 *   4. PIN throttling lives in security.ts (5 attempts → 30 s lockout).
 *
 * The gate never reveals whether a stored PIN exists; it simply requires the
 * correct one. If no PIN was ever set (defensive edge case), we force PIN
 * setup before unlocking.
 */

import {
  authenticateBiometric,
  getBiometricStatus,
  getLockoutUntil,
  isBiometricUnlockEnabled,
  isLockEnabled,
  isPinSet,
  savePin,
  verifyPin,
  PIN_LENGTH
} from './security'
import { $, clear, el } from './ui'
import { openPinPad, openPinSetup } from './pages/settings'

/** Run the gate; resolves once the user is verified (or lock is off). */
export async function runLockGate(): Promise<void> {
  const enabled = await isLockEnabled()
  if (!enabled) return

  showLockView(true)

  const pinSet = await isPinSet()
  if (!pinSet) {
    // Lock armed but no PIN on record — force enrollment before unlocking.
    await new Promise<void>((resolve) => {
      openPinSetup(() => resolve(), undefined)
    })
    showLockView(false)
    return
  }

  const bioEnabled = await isBiometricUnlockEnabled()
  const bio = await getBiometricStatus()

  if (bioEnabled && bio.available) {
    setLockStatus('正在请求生物识别…')
    const ok = await authenticateBiometric((reason) => setLockStatus(reason))
    if (ok) {
      showLockView(false)
      return
    }
    // Degrade to PIN — status line already explains why.
  } else {
    setLockStatus(bio.available ? '请输入 PIN' : `生物识别不可用 · 请输入 PIN`)
  }

  await runPinUnlock()
  showLockView(false)
}

/** PIN unlock loop with lockout display; resolves only on success. */
async function runPinUnlock(): Promise<void> {
  for (;;) {
    const lockoutUntil = await getLockoutUntil()
    if (lockoutUntil > 0) {
      await waitLockout(lockoutUntil)
    }
    const result = await new Promise<{ ok: boolean; remaining: number; lockoutUntil: number }>(
      (resolve) => {
        openPinPad({
          title: '解锁 MOA Gateway',
          subtitle: `输入 ${PIN_LENGTH} 位 PIN`,
          dismissible: false,
          onComplete: (pin) => {
            void verifyPin(pin).then(resolve)
          }
        })
      }
    )
    if (result.ok) return
    if (result.lockoutUntil > 0) {
      setLockStatus('尝试次数过多')
      await waitLockout(result.lockoutUntil)
      setLockStatus('请输入 PIN')
    } else {
      setLockStatus(`PIN 不正确，还剩 ${result.remaining} 次机会`)
    }
  }
}

async function waitLockout(until: number): Promise<void> {
  // Render a live countdown on the lock screen while waiting.
  for (;;) {
    const remainingMs = until - Date.now()
    if (remainingMs <= 0) return
    setLockStatus(`已临时锁定，请等待 ${Math.ceil(remainingMs / 1000)} 秒`)
    await sleep(Math.min(1000, remainingMs))
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function showLockView(show: boolean): void {
  const view = $('#view-lock')
  view.classList.toggle('view-active', show)
  if (show) {
    setLockStatus('正在准备…')
  }
}

function setLockStatus(message: string): void {
  const node = document.getElementById('lock-status')
  if (node) node.textContent = message
}

/** Render the static lock view content (logo + status line). */
export function initLockView(): void {
  const view = $('#view-lock')
  clear(view)
  view.append(
    el(
      'div',
      { class: 'lock-inner' },
      el('div', { class: 'lock-logo' }, '◈'),
      el('div', { class: 'lock-title' }, 'MOA Gateway'),
      el('div', { class: 'lock-status', id: 'lock-status' }, '正在准备…')
    )
  )
}
