/**
 * Small DOM utilities shared by all pages: element creation, toasts,
 * confirmation sheets, formatters. No framework — the app is intentionally a
 * thin, dependency-light layer over the gateway API.
 */

/** Typed shorthand for createElement with attributes/children. */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs?: Record<string, string>,
  ...children: Array<Node | string>
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag)
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (key === 'class') node.className = value
      else if (key === 'text') node.textContent = value
      else node.setAttribute(key, value)
    }
  }
  for (const child of children) {
    node.append(child)
  }
  return node
}

export function $(selector: string): HTMLElement {
  const node = document.querySelector(selector)
  if (!node) throw new Error(`Missing required element: ${selector}`)
  return node as HTMLElement
}

export function clear(node: HTMLElement): void {
  while (node.firstChild) node.removeChild(node.firstChild)
}

/** Deterministic accent color for a speaker name (avatar tinting). */
const SPEAKER_PALETTE = [
  '#6366F1',
  '#06B6D4',
  '#F59E0B',
  '#10B981',
  '#EC4899',
  '#8B5CF6',
  '#F97316',
  '#14B8A6'
]

export function speakerColor(name: string): string {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  }
  return SPEAKER_PALETTE[hash % SPEAKER_PALETTE.length] ?? '#6366F1'
}

export function initials(name: string): string {
  const trimmed = name.trim()
  if (!trimmed) return '?'
  // CJK names: take the first character; latin: first letters of two words.
  if (/[\u4e00-\u9fff]/.test(trimmed)) return trimmed.slice(0, 1)
  const parts = trimmed.split(/\s+/)
  if (parts.length >= 2) {
    return `${(parts[0] ?? '')[0] ?? ''}${(parts[1] ?? '')[0] ?? ''}`.toUpperCase()
  }
  return trimmed.slice(0, 2).toUpperCase()
}

/** Unix seconds → localized HH:MM (or MM-DD HH:MM for older messages). */
export function formatTime(unixSeconds: number): string {
  const d = new Date(unixSeconds * 1000)
  const now = new Date()
  const sameDay = d.toDateString() === now.toDateString()
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (sameDay) return hm
  return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${hm}`
}

export function formatLatency(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

export const MODE_LABELS: Record<string, string> = {
  round_robin: '轮流发言',
  parallel_think: '并行思考',
  free_talk: '自由讨论'
}

// ==================== Toast ====================

let toastTimer: number | undefined

export function toast(message: string, kind: 'info' | 'error' | 'success' = 'info'): void {
  const root = $('#toast-root')
  clear(root)
  const node = el('div', { class: `toast toast-${kind}`, role: 'status' }, message)
  root.append(node)
  // Force reflow so the entrance transition plays.
  void node.offsetWidth
  node.classList.add('toast-in')
  if (toastTimer) window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => {
    node.classList.remove('toast-in')
    window.setTimeout(() => node.remove(), 250)
  }, 3200)
}

// ==================== Modal / sheet ====================

/**
 * Render content inside the modal root with a dimmed backdrop.
 * Returns a close function; backdrop tap closes unless `dismissible` is false.
 */
export function openSheet(
  build: (close: () => void) => Node,
  options: { dismissible?: boolean } = {}
): () => void {
  const { dismissible = true } = options
  const root = $('#modal-root')
  clear(root)
  const backdrop = el('div', { class: 'sheet-backdrop' })
  const sheet = el('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true' })
  const close = (): void => {
    backdrop.classList.add('sheet-out')
    sheet.classList.add('sheet-out')
    window.setTimeout(() => {
      backdrop.remove()
      sheet.remove()
    }, 200)
  }
  if (dismissible) {
    backdrop.addEventListener('click', close)
  }
  sheet.append(build(close))
  root.append(backdrop, sheet)
  requestAnimationFrame(() => sheet.classList.add('sheet-in'))
  return close
}

export function confirmSheet(
  title: string,
  message: string,
  confirmLabel: string,
  onConfirm: () => void,
  danger = true
): void {
  let closeFn: (() => void) | undefined
  const content = el(
    'div',
    { class: 'confirm-box' },
    el('h3', { class: 'confirm-title' }, title),
    el('p', { class: 'confirm-message' }, message)
  )
  const actions = el('div', { class: 'confirm-actions' })
  const cancelBtn = el('button', { class: 'btn btn-secondary btn-block' }, '取消')
  const okBtn = el(
    'button',
    { class: `btn ${danger ? 'btn-danger' : 'btn-primary'} btn-block` },
    confirmLabel
  )
  cancelBtn.addEventListener('click', () => closeFn?.())
  okBtn.addEventListener('click', () => {
    closeFn?.()
    onConfirm()
  })
  actions.append(cancelBtn, okBtn)
  content.append(actions)
  closeFn = openSheet(() => content)
}
