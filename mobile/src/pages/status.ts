/**
 * Status page — live health of the gateway.
 *
 * Pulls /health (version + endpoint counts) and /health/ready (component
 * readiness) and renders a traffic-light summary. Auto-refreshes every 15 s
 * unless the user turned it off (the preference is persisted).
 */

import { getClient } from '../state'
import { getBool, setBool, StorageKeys } from '../store'
import type { HealthSummary, ReadinessSummary } from '../types'
import { $, clear, el, formatLatency } from '../ui'

const AUTO_REFRESH_INTERVAL_MS = 15_000
let refreshTimer: number | undefined
let autoRefresh = true
let refreshing = false

function statusTone(status: string | undefined): 'ok' | 'warn' | 'bad' {
  if (status === 'healthy' || status === 'ok') return 'ok'
  if (status === 'degraded') return 'warn'
  return 'bad'
}

function lampClass(tone: 'ok' | 'warn' | 'bad'): string {
  return `lamp lamp-${tone}`
}

const TONE_LABEL: Record<'ok' | 'warn' | 'bad', string> = {
  ok: '运行正常',
  warn: '部分降级',
  bad: '不可用'
}

export async function renderStatus(): Promise<void> {
  const page = $('#page-status')
  clear(page)

  const client = getClient()
  if (!client) {
    page.append(
      el(
        'div',
        { class: 'empty-state' },
        el('div', { class: 'empty-icon' }, '⚠'),
        el('p', { class: 'empty-text' }, '尚未配置网关，请先到「设置」填写网关地址。')
      )
    )
    return
  }

  // Header with refresh + auto-refresh toggle.
  const header = el(
    'div',
    { class: 'status-header' },
    el('div', { class: 'status-title-wrap' }, el('h2', { class: 'page-title' }, '网关状态')),
    el(
      'div',
      { class: 'status-controls' },
      (() => {
        const auto = el('button', {
          class: `btn btn-ghost btn-sm ${autoRefresh ? 'is-active' : ''}`,
          id: 'btn-auto-refresh'
        }, autoRefresh ? '自动刷新：开' : '自动刷新：关')
        auto.addEventListener('click', () => void toggleAutoRefresh())
        return auto
      })(),
      (() => {
        const btn = el('button', { class: 'btn btn-ghost btn-sm', id: 'btn-refresh' }, '刷新')
        btn.addEventListener('click', () => void refresh())
        return btn
      })()
    )
  )
  page.append(header)

  const lamp = el('div', { class: 'lamp-wrap' }, el('div', { class: 'lamp lamp-bad', id: 'status-lamp' }))
  const lampLabel = el('div', { class: 'lamp-label', id: 'status-lamp-label' }, '检测中…')
  const lampUrl = el('div', { class: 'lamp-url' }, client.url)
  page.append(el('div', { class: 'status-hero card' }, lamp, lampLabel, lampUrl))

  const grid = el('div', { class: 'status-grid', id: 'status-grid' })
  page.append(grid)

  const components = el('div', { class: 'card', id: 'status-components' })
  components.append(el('h3', { class: 'card-title' }, '组件就绪状态'))
  page.append(components)

  await refresh()
}

async function toggleAutoRefresh(): Promise<void> {
  autoRefresh = !autoRefresh
  await setBool(StorageKeys.autoRefreshStatus, autoRefresh)
  const btn = document.getElementById('btn-auto-refresh')
  if (btn) {
    btn.textContent = autoRefresh ? '自动刷新：开' : '自动刷新：关'
    btn.classList.toggle('is-active', autoRefresh)
  }
  scheduleRefresh()
}

function scheduleRefresh(): void {
  if (refreshTimer) window.clearInterval(refreshTimer)
  if (autoRefresh) {
    refreshTimer = window.setInterval(() => void refresh(), AUTO_REFRESH_INTERVAL_MS)
  }
}

export function stopStatusRefresh(): void {
  if (refreshTimer) window.clearInterval(refreshTimer)
  refreshTimer = undefined
}

async function refresh(): Promise<void> {
  if (refreshing) return
  refreshing = true
  const client = getClient()
  const lamp = document.getElementById('status-lamp')
  const lampLabel = document.getElementById('status-lamp-label')
  if (!client || !lamp || !lampLabel) {
    refreshing = false
    return
  }

  let health: HealthSummary | null = null
  let readiness: ReadinessSummary | null = null
  let overall: 'ok' | 'warn' | 'bad' = 'bad'
  let failureMessage = ''

  try {
    readiness = await client.readiness()
  } catch (err) {
    failureMessage = err instanceof Error ? err.message : String(err)
  }
  try {
    health = await client.health()
  } catch {
    // Readiness is the source of truth; /health failure alone is not fatal.
  }

  if (readiness) {
    overall = statusTone(readiness.status)
  }

  lamp.className = lampClass(overall)
  lampLabel.textContent = readiness ? TONE_LABEL[overall] : '无法连接'

  renderGrid(health, readiness, failureMessage)
  renderComponents(readiness, failureMessage)
  refreshing = false
}

function renderGrid(
  health: HealthSummary | null,
  readiness: ReadinessSummary | null,
  failureMessage: string
): void {
  const grid = document.getElementById('status-grid')
  if (!grid) return
  clear(grid)

  if (!readiness && !health) {
    grid.append(
      el(
        'div',
        { class: 'card status-error' },
        el('div', { class: 'status-error-title' }, '无法连接网关'),
        el('div', { class: 'status-error-msg' }, failureMessage || '请检查网关地址与网络。')
      )
    )
    return
  }

  const cells: Array<{ label: string; value: string; tone?: 'ok' | 'warn' | 'bad' }> = []
  if (health) {
    cells.push({ label: '版本', value: health.version || '—' })
    cells.push({
      label: '健康端点',
      value: `${health.endpoints_healthy} / ${health.endpoints_enabled}`,
      tone: health.endpoints_healthy > 0 ? 'ok' : 'warn'
    })
    cells.push({ label: '端点总数', value: String(health.endpoints_total) })
    cells.push({
      label: '真实 / Mock',
      value: `${health.real_endpoints_count} / ${health.mock_endpoints_count}`,
      tone: health.mock_endpoints_count > 0 ? 'warn' : 'ok'
    })
    cells.push({ label: 'Mock 模式', value: health.mock_mode || '—' })
  }
  if (readiness) {
    cells.push({ label: '就绪状态', value: readiness.status, tone: statusTone(readiness.status) })
  }

  for (const cell of cells) {
    const value = el('div', { class: 'metric-value' }, cell.value)
    if (cell.tone) value.classList.add(`tone-${cell.tone}`)
    grid.append(el('div', { class: 'card metric-card' }, value, el('div', { class: 'metric-label' }, cell.label)))
  }
}

function renderComponents(readiness: ReadinessSummary | null, failureMessage: string): void {
  const box = document.getElementById('status-components')
  if (!box) return
  clear(box)
  box.append(el('h3', { class: 'card-title' }, '组件就绪状态'))

  if (!readiness) {
    box.append(el('p', { class: 'muted' }, failureMessage || '未获取到就绪信息。'))
    return
  }
  const components = readiness.components ?? {}
  const names = Object.keys(components)
  if (names.length === 0) {
    box.append(el('p', { class: 'muted' }, `整体状态：${readiness.status}（无组件明细）`))
    return
  }
  const list = el('div', { class: 'component-list' })
  for (const name of names) {
    const comp = components[name]
    if (!comp) continue
    const tone = statusTone(comp.status)
    const row = el(
      'div',
      { class: 'component-row' },
      el('div', { class: `dot ${lampClass(tone)}` }),
      el('div', { class: 'component-name' }, name),
      el('div', { class: 'component-latency' }, formatLatency(comp.latency_ms))
    )
    list.append(row)
  }
  box.append(list)
}

export async function initStatusPrefs(): Promise<void> {
  autoRefresh = await getBool(StorageKeys.autoRefreshStatus, true)
}

export function startStatusPage(): void {
  scheduleRefresh()
}
