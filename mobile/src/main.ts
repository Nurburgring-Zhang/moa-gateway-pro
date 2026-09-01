/**
 * App entry point — boot sequence, view routing, bottom tab navigation.
 *
 * Boot order (mirrors the required gate logic):
 *   1. Load persisted gateway config + UI prefs.
 *   2. Run the lock gate (biometric → PIN fallback) when the app lock is on.
 *   3. If a gateway is configured → main shell (Status / Dialogue / Settings).
 *      Otherwise → setup screen.
 *
 * Views are plain <section> elements toggled with .view-active; there is no
 * router library. The room chat is a full-screen overlay view on top of the
 * shell so tab state survives entering/leaving a conversation.
 */

import { initLockView, runLockGate } from './lock'
import { renderDialogue, loadRooms } from './pages/dialogue'
import { closeRoom, sendMessage } from './pages/room'
import { renderSettings, openPinSetup } from './pages/settings'
import { showSetup } from './pages/setup'
import { initStatusPrefs, renderStatus, startStatusPage, stopStatusRefresh } from './pages/status'
import {
  getBiometricStatus,
  savePin,
  setBiometricUnlockEnabled,
  setLockEnabled
} from './security'
import { hasGateway, loadPersistedConfig } from './state'
import { getBool, setBool, StorageKeys } from './store'
import { $, el, openSheet } from './ui'

type Tab = 'status' | 'dialogue' | 'settings'

let activeTab: Tab = 'status'
let booted = false

async function boot(): Promise<void> {
  initLockView()
  wireTabBar()
  wireRoomChrome()
  await initStatusPrefs()
  await loadPersistedConfig()

  // App-start gate: biometric first, PIN fallback (see lock.ts).
  await runLockGate()

  enterPostLock()
}

function enterPostLock(): void {
  if (hasGateway()) {
    showMain()
  } else {
    showSetup({ returnable: false })
  }
}

function showMain(): void {
  $('#view-setup').classList.remove('view-active')
  $('#view-lock').classList.remove('view-active')
  $('#view-main').classList.add('view-active')
  void switchTab(activeTab)
}

async function switchTab(tab: Tab): Promise<void> {
  activeTab = tab
  stopStatusRefresh()
  for (const t of ['status', 'dialogue', 'settings'] as Tab[]) {
    $(`#page-${t}`).classList.toggle('page-active', t === tab)
    const btn = document.getElementById(`tab-${t}`)
    btn?.classList.toggle('is-active', t === tab)
    btn?.setAttribute('aria-selected', String(t === tab))
  }
  if (tab === 'status') {
    await renderStatus()
    startStatusPage()
  } else if (tab === 'dialogue') {
    await renderDialogue()
  } else {
    await renderSettings()
  }
}

function wireTabBar(): void {
  const tabs: Tab[] = ['status', 'dialogue', 'settings']
  for (const tab of tabs) {
    const btn = document.getElementById(`tab-${tab}`)
    btn?.addEventListener('click', () => void switchTab(tab))
  }
}

function wireRoomChrome(): void {
  $('#room-back').addEventListener('click', () => {
    closeRoom()
    // Refresh the room list so round counters/timestamps are current.
    if (activeTab === 'dialogue') void loadRooms()
  })
  const input = document.getElementById('room-input') as HTMLTextAreaElement | null
  const send = document.getElementById('room-send')
  send?.addEventListener('click', () => void sendMessage())
  input?.addEventListener('keydown', (e) => {
    // Enter sends; Shift+Enter inserts a newline (desktop keyboards).
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void sendMessage()
    }
  })
}

// After setup saves a config, (re)enter the main shell.
window.addEventListener('moa:gateway-configured', () => {
  if (!booted) return
  showMain()
})

// First-run hint: offer to arm the app lock once the user is in. We only ask
// once; the answer (yes or no) is persisted.
async function maybeOfferLockSetup(): Promise<void> {
  const decided = await getBool(StorageKeys.lockOfferDecided, false)
  const lockOn = await getBool(StorageKeys.lockEnabled, false)
  if (decided || lockOn) return
  await setBool(StorageKeys.lockOfferDecided, true)
  // One-time offer card; the decision (either button) is persisted above.
  openSheet((close) =>
    el(
      'div',
      { class: 'sheet-body' },
      el('div', { class: 'sheet-handle' }),
      el('h3', { class: 'sheet-title' }, '开启应用锁？'),
      el(
        'p',
        { class: 'muted' },
        '控制台里保存着网关 API Key。建议开启应用锁（生物识别 + PIN），防止他人使用本机直接访问网关。'
      ),
      el(
        'div',
        { class: 'confirm-actions' },
        (() => {
          const later = el('button', { class: 'btn btn-secondary btn-block' }, '以后再说')
          later.addEventListener('click', close)
          return later
        })(),
        (() => {
          const yes = el('button', { class: 'btn btn-primary btn-block' }, '立即设置')
          yes.addEventListener('click', () => {
            close()
            openPinSetup(async (pin) => {
              await savePin(pin)
              await setLockEnabled(true)
              const bio = await getBiometricStatus()
              await setBiometricUnlockEnabled(bio.available)
              if (activeTab === 'settings') await renderSettings()
            }, undefined)
          })
          return yes
        })()
      )
    )
  )
}

void boot().then(() => {
  booted = true
  if (hasGateway()) void maybeOfferLockSetup()
})
