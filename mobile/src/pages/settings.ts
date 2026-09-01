/**
 * Settings page — gateway connection, app-lock security, about.
 *
 * Security model:
 *  - Enabling the app lock requires setting a 6-digit PIN first (the PIN is
 *    the mandatory fallback whenever biometry is unavailable or fails).
 *  - The biometric toggle is only offered when the device reports enrolled
 *    biometry; otherwise it is shown disabled with the real reason.
 */

import {
  authenticateBiometric,
  clearPin,
  getBiometricStatus,
  isBiometricUnlockEnabled,
  isLockEnabled,
  isPinSet,
  savePin,
  setBiometricUnlockEnabled,
  setLockEnabled,
  verifyPin,
  PIN_LENGTH,
  type BiometricStatus
} from '../security'
import { clearPersistedConfig, getConfig } from '../state'
import { $, clear, confirmSheet, el, openSheet, toast } from '../ui'
import { showSetup } from './setup'

export async function renderSettings(): Promise<void> {
  const page = $('#page-settings')
  clear(page)
  page.append(el('div', { class: 'page-header' }, el('h2', { class: 'page-title' }, '设置')))

  const config = getConfig()

  // ============ Gateway section ============
  const gatewayCard = el('div', { class: 'card settings-card' }, el('h3', { class: 'card-title' }, '网关连接'))
  if (config) {
    gatewayCard.append(
      settingsRow('网关地址', config.baseUrl),
      settingsRow('API Key', config.apiKey ? '••••••••' + config.apiKey.slice(-4) : '（未设置）')
    )
    const changeBtn = el('button', { class: 'btn btn-secondary btn-block' }, '修改网关配置')
    changeBtn.addEventListener('click', () => showSetup({ returnable: true }))
    gatewayCard.append(el('div', { class: 'settings-actions' }, changeBtn))
  } else {
    gatewayCard.append(el('p', { class: 'muted' }, '尚未配置网关。'))
    const goBtn = el('button', { class: 'btn btn-primary btn-block' }, '前往配置')
    goBtn.addEventListener('click', () => showSetup({ returnable: true }))
    gatewayCard.append(el('div', { class: 'settings-actions' }, goBtn))
  }
  page.append(gatewayCard)

  // ============ Security section ============
  const securityCard = el(
    'div',
    { class: 'card settings-card' },
    el('h3', { class: 'card-title' }, '安全'),
    el('p', { class: 'settings-hint' }, '应用启动门禁：优先生物识别，失败自动降级到 PIN。')
  )

  const lockEnabled = await isLockEnabled()
  const biometricEnabled = await isBiometricUnlockEnabled()
  const biometry: BiometricStatus = await getBiometricStatus()
  const pinSet = await isPinSet()

  // App lock master toggle
  const lockToggle = toggleRow(
    '应用锁',
    '启动 App 时要求验证身份',
    lockEnabled,
    async (next) => {
      if (next) {
        if (!pinSet) {
          // Must define a PIN before the lock can be armed.
          openPinSetup(async (pin) => {
            await savePin(pin)
            await setLockEnabled(true)
            toast('应用锁已开启', 'success')
            await renderSettings()
          }, () => void renderSettings())
        } else {
          await setLockEnabled(true)
          toast('应用锁已开启', 'success')
          await renderSettings()
        }
      } else {
        await setLockEnabled(false)
        toast('应用锁已关闭', 'info')
        await renderSettings()
      }
    }
  )
  securityCard.append(lockToggle)

  // Biometric toggle (only meaningful when lock is on)
  const bioRow = toggleRow(
    '生物识别解锁',
    biometry.available ? biometry.detail : `不可用：${biometry.detail}`,
    lockEnabled && biometricEnabled && biometry.available,
    async (next) => {
      if (!lockEnabled) {
        toast('请先开启应用锁', 'error')
        await renderSettings()
        return
      }
      if (!biometry.available) {
        toast(`当前设备无法使用生物识别：${biometry.detail}`, 'error')
        await renderSettings()
        return
      }
      await setBiometricUnlockEnabled(next)
      await renderSettings()
    }
  )
  if (!biometry.available || !lockEnabled) bioRow.classList.add('is-disabled')
  securityCard.append(bioRow)

  // Change PIN (only when lock is armed)
  if (lockEnabled && pinSet) {
    const pinBtn = el('button', { class: 'btn btn-secondary btn-block' }, '修改 PIN')
    pinBtn.addEventListener('click', () => {
      openPinChangeFlow()
    })
    securityCard.append(el('div', { class: 'settings-actions' }, pinBtn))
  }
  page.append(securityCard)

  // ============ About section ============
  const aboutCard = el(
    'div',
    { class: 'card settings-card' },
    el('h3', { class: 'card-title' }, '关于'),
    settingsRow('客户端', 'MOA Gateway Console 1.0.0 (Capacitor Android)'),
    settingsRow('目标网关', config ? config.baseUrl : '未配置'),
    el(
      'p',
      { class: 'settings-hint' },
      '本客户端直连局域网内的 MOA Gateway FastAPI 服务，所有请求携带 Authorization: Bearer <API Key>。'
    )
  )
  page.append(aboutCard)

  // Danger zone
  if (config) {
    const dangerCard = el('div', { class: 'card settings-card' })
    const clearBtn = el('button', { class: 'btn btn-danger btn-block' }, '清除网关配置')
    clearBtn.addEventListener('click', () => {
      confirmSheet(
        '清除网关配置',
        '将删除保存的网关地址与 API Key，下次启动需重新配置。',
        '清除',
        () => {
          void clearPersistedConfig().then(() => {
            toast('已清除网关配置', 'info')
            void renderSettings()
          })
        }
      )
    })
    dangerCard.append(el('div', { class: 'settings-actions' }, clearBtn))
    page.append(dangerCard)
  }
}

function settingsRow(label: string, value: string): HTMLElement {
  return el(
    'div',
    { class: 'settings-row' },
    el('div', { class: 'settings-row-label' }, label),
    el('div', { class: 'settings-row-value' }, value)
  )
}

function toggleRow(
  label: string,
  hint: string,
  checked: boolean,
  onChange: (next: boolean) => Promise<void> | void
): HTMLElement {
  const knob = el('div', { class: `toggle ${checked ? 'is-on' : ''}` }, el('div', { class: 'toggle-knob' }))
  const row = el(
    'div',
    { class: 'settings-row settings-row-toggle', role: 'switch', 'aria-checked': String(checked), tabindex: '0' },
    el('div', { class: 'settings-row-text' }, el('div', { class: 'settings-row-label' }, label), el('div', { class: 'settings-row-hint' }, hint)),
    knob
  )
  let busy = false
  const handle = (): void => {
    if (busy) return
    busy = true
    void Promise.resolve(onChange(!checked)).finally(() => {
      busy = false
    })
  }
  row.addEventListener('click', handle)
  row.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      handle()
    }
  })
  return row
}

// ==================== PIN flows ====================

/**
 * Shared PIN pad renderer. `onComplete` receives the entered PIN.
 * The pad is used for: first-time setup (enter twice), change (verify old,
 * then enter new twice). Verification against the stored hash is done by the
 * caller via verifyPin().
 */
export function openPinPad(options: {
  title: string
  subtitle?: string
  onComplete: (pin: string) => void
  onCancel?: () => void
  dismissible?: boolean
}): void {
  let entry = ''
  openSheet((close) => {
    const body = el('div', { class: 'sheet-body pin-pad-body' })
    body.append(el('div', { class: 'sheet-handle' }))
    body.append(el('h3', { class: 'sheet-title' }, options.title))
    if (options.subtitle) body.append(el('p', { class: 'pin-subtitle', id: 'pin-subtitle' }, options.subtitle))

    const dots = el('div', { class: 'pin-dots' })
    for (let i = 0; i < PIN_LENGTH; i++) dots.append(el('div', { class: 'pin-dot' }))
    body.append(dots)

    const errorLine = el('div', { class: 'pin-error', id: 'pin-error' })
    body.append(errorLine)

    const renderDots = (): void => {
      const dotNodes = dots.querySelectorAll('.pin-dot')
      dotNodes.forEach((d, i) => d.classList.toggle('is-filled', i < entry.length))
    }

    const keypad = el('div', { class: 'pin-keypad' })
    const press = (digit: string): void => {
      if (entry.length >= PIN_LENGTH) return
      entry += digit
      errorLine.textContent = ''
      renderDots()
      if (entry.length === PIN_LENGTH) {
        const pin = entry
        entry = ''
        // Small delay so the last dot visibly fills before the next step.
        window.setTimeout(() => {
          close()
          options.onComplete(pin)
        }, 120)
      }
    }
    for (const key of ['1', '2', '3', '4', '5', '6', '7', '8', '9']) {
      const btn = el('button', { class: 'pin-key', type: 'button' }, key)
      btn.addEventListener('click', () => press(key))
      keypad.append(btn)
    }
    keypad.append(el('div', { class: 'pin-key pin-key-blank' }))
    const zero = el('button', { class: 'pin-key', type: 'button' }, '0')
    zero.addEventListener('click', () => press('0'))
    keypad.append(zero)
    const back = el('button', { class: 'pin-key pin-key-action', type: 'button' }, '⌫')
    back.addEventListener('click', () => {
      entry = entry.slice(0, -1)
      renderDots()
    })
    keypad.append(back)
    body.append(keypad)

    if (options.onCancel) {
      const cancel = el('button', { class: 'btn btn-ghost btn-block' }, '取消')
      cancel.addEventListener('click', () => {
        close()
        options.onCancel?.()
      })
      body.append(cancel)
    }
    renderDots()
    return body
  }, { dismissible: options.dismissible ?? true })
}

/** First-time PIN setup: enter twice, must match. */
export function openPinSetup(onSaved: (pin: string) => void, onCancel?: () => void): void {
  openPinPad({
    title: '设置 PIN',
    subtitle: `输入 ${PIN_LENGTH} 位数字 PIN（第 1 次）`,
    dismissible: !!onCancel,
    onCancel,
    onComplete: (first) => {
      openPinPad({
        title: '确认 PIN',
        subtitle: `再次输入 ${PIN_LENGTH} 位数字 PIN（第 2 次）`,
        dismissible: false,
        onComplete: (second) => {
          if (first === second) {
            onSaved(first)
          } else {
            toast('两次输入不一致，请重新设置', 'error')
            openPinSetup(onSaved, onCancel)
          }
        }
      })
    }
  })
}

/** Change PIN: verify current PIN, then set a new one (enter twice). */
function openPinChangeFlow(): void {
  openPinPad({
    title: '验证当前 PIN',
    subtitle: '输入当前 PIN 以继续',
    onCancel: () => undefined,
    onComplete: (pin) => {
      void verifyPin(pin).then((result) => {
        if (result.ok) {
          openPinSetup(async (newPin) => {
            await savePin(newPin)
            toast('PIN 已更新', 'success')
          }, () => undefined)
        } else if (result.lockoutUntil > 0) {
          toast('尝试次数过多，请稍后再试', 'error')
        } else {
          toast(`PIN 不正确，还剩 ${result.remaining} 次机会`, 'error')
          openPinChangeFlow()
        }
      })
    }
  })
}

/** Verify identity with biometry-or-PIN for a sensitive in-session action. */
export async function stepUpAuth(reason: string): Promise<boolean> {
  const lockOn = await isLockEnabled()
  if (!lockOn) return true
  const bioOn = await isBiometricUnlockEnabled()
  const bio = await getBiometricStatus()
  if (bioOn && bio.available) {
    const ok = await authenticateBiometric(() => undefined)
    if (ok) return true
    // fall through to PIN
  }
  return new Promise<boolean>((resolve) => {
    openPinPad({
      title: '身份验证',
      subtitle: reason,
      onCancel: () => resolve(false),
      onComplete: (pin) => {
        void verifyPin(pin).then((result) => {
          if (result.ok) resolve(true)
          else {
            toast(result.lockoutUntil > 0 ? '尝试次数过多，已临时锁定' : 'PIN 不正确', 'error')
            resolve(false)
          }
        })
      }
    })
  })
}
