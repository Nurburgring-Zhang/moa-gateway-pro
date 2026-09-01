/**
 * Setup page — gateway address + API key configuration.
 *
 * The "测试连接" button performs a REAL probe: fetch GET /health/ready (no
 * auth needed) and, on success, GET /health for version/endpoint metadata.
 * If an API key is entered we additionally validate it against GET /v1/models
 * (401 ⇒ invalid key) so users get an honest signal before saving.
 */

import { GatewayClient, GatewayError, normalizeBaseUrl, probeGateway } from '../gateway'
import { getConfig, persistConfig } from '../state'
import type { ProbeResult } from '../types'
import { $, clear, el, toast } from '../ui'

interface SetupOptions {
  /** When true a back button returns to the main shell (editing existing config). */
  returnable: boolean
}

export function showSetup(options: SetupOptions = { returnable: false }): void {
  $('#view-main').classList.remove('view-active')
  $('#view-setup').classList.add('view-active')
  renderSetupForm(options)
}

export function hideSetup(): void {
  $('#view-setup').classList.remove('view-active')
}

function renderSetupForm(options: SetupOptions): void {
  const page = $('#setup-content')
  clear(page)

  const existing = getConfig()

  const header = el(
    'div',
    { class: 'setup-header' },
    el('div', { class: 'setup-logo' }, '◈'),
    el('h1', { class: 'setup-title' }, 'MOA Gateway'),
    el('p', { class: 'setup-subtitle' }, '连接到局域网内的多 AI 网关服务')
  )
  page.append(header)

  if (options.returnable) {
    const back = el('button', { class: 'btn btn-ghost btn-sm setup-back' }, '← 返回')
    back.addEventListener('click', () => {
      hideSetup()
      $('#view-main').classList.add('view-active')
    })
    page.prepend(back)
  }

  const card = el('div', { class: 'card setup-card' })

  const urlInput = el('input', {
    class: 'input',
    id: 'setup-url',
    type: 'url',
    inputmode: 'url',
    placeholder: 'http://192.168.1.10:8910',
    autocomplete: 'off',
    spellcheck: 'false'
  }) as HTMLInputElement
  if (existing) urlInput.value = existing.baseUrl
  card.append(
    el('label', { class: 'field-label', for: 'setup-url' }, '网关地址'),
    urlInput,
    el(
      'p',
      { class: 'field-hint' },
      '网关默认端口 8910（moa_gateway/config.py ServerConfig.port）。手机需与网关在同一局域网。'
    )
  )

  const keyWrap = el('div', { class: 'input-with-action' })
  const keyInput = el('input', {
    class: 'input',
    id: 'setup-key',
    type: 'password',
    placeholder: '网关 API Key（Authorization: Bearer）',
    autocomplete: 'off',
    spellcheck: 'false'
  }) as HTMLInputElement
  if (existing) keyInput.value = existing.apiKey
  const eye = el('button', { class: 'btn btn-ghost btn-sm', type: 'button', 'aria-label': '显示/隐藏 Key' }, '👁')
  eye.addEventListener('click', () => {
    keyInput.type = keyInput.type === 'password' ? 'text' : 'password'
  })
  keyWrap.append(keyInput, eye)
  card.append(
    el('label', { class: 'field-label', for: 'setup-key' }, 'API Key'),
    keyWrap,
    el(
      'p',
      { class: 'field-hint' },
      '在网关 Web 控制台（浏览器访问网关根路径）的 API Key 管理中生成。'
    )
  )

  const resultBox = el('div', { class: 'probe-result', id: 'probe-result' })
  const probeBtn = el('button', { class: 'btn btn-secondary btn-block btn-lg' }, '测试连接')
  probeBtn.addEventListener('click', () => void runProbe(urlInput, keyInput, probeBtn, resultBox))
  card.append(el('div', { class: 'setup-actions' }, probeBtn), resultBox)
  page.append(card)

  const saveBtn = el('button', { class: 'btn btn-primary btn-block btn-lg', id: 'setup-save' }, '保存并进入')
  saveBtn.addEventListener('click', () => void saveConfig(urlInput, keyInput))
  page.append(saveBtn)
}

async function runProbe(
  urlInput: HTMLInputElement,
  keyInput: HTMLInputElement,
  btn: HTMLButtonElement,
  resultBox: HTMLElement
): Promise<void> {
  const rawUrl = urlInput.value.trim()
  if (!rawUrl) {
    renderProbeResult(resultBox, {
      ok: false,
      httpStatus: 0,
      latencyMs: 0,
      message: '请先输入网关地址'
    })
    return
  }
  btn.disabled = true
  btn.textContent = '探测中…'
  clear(resultBox)
  resultBox.append(el('div', { class: 'probe-loading' }, '正在请求 /health/ready …'))

  const result = await probeGateway(rawUrl)

  // If reachable and a key was provided, validate the key too.
  let keyStatus: string | null = null
  if (result.ok && keyInput.value.trim()) {
    try {
      const { GatewayClient } = await import('../gateway')
      const client = new GatewayClient({
        baseUrl: normalizeBaseUrl(rawUrl),
        apiKey: keyInput.value.trim()
      })
      await client.listModels()
      keyStatus = 'API Key 验证通过'
    } catch (err) {
      if (err instanceof GatewayError && err.status === 401) {
        keyStatus = 'API Key 无效（401）— 对话功能将不可用'
        result.ok = false
        result.message = keyStatus
      } else {
        keyStatus = `API Key 校验失败：${err instanceof Error ? err.message : String(err)}`
      }
    }
  }
  renderProbeResult(resultBox, result, keyStatus)
  btn.disabled = false
  btn.textContent = '测试连接'
}

function renderProbeResult(box: HTMLElement, result: ProbeResult, keyStatus?: string | null): void {
  clear(box)
  const cls = result.ok ? 'probe-ok' : 'probe-fail'
  const icon = result.ok ? '✓' : '✕'
  const node = el(
    'div',
    { class: `probe-box ${cls}` },
    el(
      'div',
      { class: 'probe-head' },
      el('span', { class: 'probe-icon' }, icon),
      el('span', { class: 'probe-msg' }, result.message)
    )
  )
  const details: string[] = []
  if (result.latencyMs > 0) details.push(`探测耗时 ${result.latencyMs} ms`)
  if (result.health) {
    details.push(
      `版本 v${result.health.version} · 端点 ${result.health.endpoints_healthy}/${result.health.endpoints_enabled} 健康`
    )
    if (result.health.mock_endpoints_count > 0) {
      details.push(`注意：${result.health.mock_endpoints_count} 个 mock 端点（无真实 Key）`)
    }
  }
  if (keyStatus) details.push(keyStatus)
  if (details.length > 0) {
    node.append(el('div', { class: 'probe-details' }, details.join(' · ')))
  }
  box.append(node)
}

async function saveConfig(urlInput: HTMLInputElement, keyInput: HTMLInputElement): Promise<void> {
  const rawUrl = urlInput.value.trim()
  let baseUrl: string
  try {
    baseUrl = normalizeBaseUrl(rawUrl)
  } catch {
    toast('URL 格式无效', 'error')
    return
  }
  if (!baseUrl) {
    toast('请输入网关地址', 'error')
    return
  }
  const apiKey = keyInput.value.trim()
  await persistConfig({ baseUrl, apiKey })
  toast('网关配置已保存', 'success')
  hideSetup()
  // main.ts listens for this to (re)build the authenticated shell.
  window.dispatchEvent(new CustomEvent('moa:gateway-configured'))
}
