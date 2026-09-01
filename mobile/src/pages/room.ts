/**
 * Room chat view — full-screen conversation with live SSE streaming.
 *
 * Data flow for one user turn:
 *   1. User taps send → optimistic user bubble + composer disabled.
 *   2. POST /v1/dialogue/rooms/{id}/messages?stream=true starts the turn.
 *      This call blocks until the whole multi-AI round finishes.
 *   3. Meanwhile the SSE stream (opened when the room was entered) pushes
 *      DialogueEvents: message_start → delta* → message_end per speaker.
 *      We create a bubble per speaker on message_start and append deltas live.
 *   4. When the POST resolves, its `responses` array is the authoritative
 *      result — we reconcile it against the live bubbles so nothing is lost
 *      even if the stream dropped mid-turn.
 *
 * The stream is subscribed with replay=false because persisted history is
 * loaded separately via GET /v1/dialogue/rooms/{id}; replaying the engine's
 * in-memory buffer would duplicate messages.
 */

import { GatewayError } from '../gateway'
import { getClient } from '../state'
import type { DialogueEvent, DialogueMessage, DialogueRoom } from '../types'
import { $, clear, el, formatLatency, initials, MODE_LABELS, speakerColor, toast } from '../ui'
import { describeError } from './dialogue'

interface BubbleHandle {
  root: HTMLElement
  contentEl: HTMLElement
  text: string
  finalized: boolean
}

interface RoomSession {
  roomId: string
  room: DialogueRoom | null
  /** Live bubbles keyed by `${round}:${speaker}`. */
  bubbles: Map<string, BubbleHandle>
  closeStream: (() => void) | null
  streamRetries: number
  turnActive: boolean
  /** Content of the optimistically rendered user message (dedupe on echo). */
  pendingUserContent: string | null
  abortPost: AbortController | null
  destroyed: boolean
}

let session: RoomSession | null = null

const MAX_STREAM_RETRIES = 3

export function openRoom(roomId: string): void {
  closeRoomSession()
  session = {
    roomId,
    room: null,
    bubbles: new Map(),
    closeStream: null,
    streamRetries: 0,
    turnActive: false,
    pendingUserContent: null,
    abortPost: null,
    destroyed: false
  }
  showRoomView(true)
  void loadRoom(roomId)
}

export function closeRoom(): void {
  closeRoomSession()
  showRoomView(false)
}

function closeRoomSession(): void {
  if (session) {
    session.destroyed = true
    if (session.closeStream) session.closeStream()
    if (session.abortPost) session.abortPost.abort()
  }
  session = null
}

function showRoomView(show: boolean): void {
  const view = $('#view-room')
  view.classList.toggle('view-active', show)
}

async function loadRoom(roomId: string): Promise<void> {
  const client = getClient()
  const view = $('#view-room')
  if (!client || !session) return

  const body = $('#room-messages')
  clear(body)
  body.append(el('div', { class: 'list-loading' }, '加载对话记录…'))
  setHeader({ topic: '加载中…', mode: '', participants: [] })
  setComposerEnabled(false)

  try {
    const detail = await client.getRoom(roomId)
    if (!session || session.destroyed || session.roomId !== roomId) return
    session.room = detail.room
    setHeader({
      topic: detail.room.topic,
      mode: detail.room.mode,
      participants: detail.room.participants.map((p) => p.name)
    })
    clear(body)
    if (detail.messages.length === 0) {
      body.append(
        el('p', { class: 'room-empty-hint' }, '房间已就绪。发送第一条消息，触发一轮多 AI 响应。')
      )
    }
    for (const msg of detail.messages) {
      appendHistoryMessage(msg)
    }
    scrollToBottom(true)
    setComposerEnabled(true)
    connectStream(roomId)
  } catch (err) {
    clear(body)
    body.append(
      el(
        'div',
        { class: 'empty-state' },
        el('div', { class: 'empty-icon' }, '✕'),
        el('p', { class: 'empty-text' }, describeError(err))
      )
    )
  }
  void view
}

function setHeader(info: { topic: string; mode: string; participants: string[] }): void {
  const title = $('#room-title')
  const meta = $('#room-meta')
  clear(title)
  clear(meta)
  title.append(info.topic)
  if (info.mode) {
    meta.append(el('span', { class: `badge badge-mode-${info.mode}` }, MODE_LABELS[info.mode] ?? info.mode))
  }
  if (info.participants.length > 0) {
    meta.append(el('span', { class: 'room-meta-names' }, info.participants.join(' · ')))
  }
}

// ==================== Rendering ====================

function appendHistoryMessage(msg: DialogueMessage): void {
  const body = $('#room-messages')
  if (msg.role === 'user') {
    body.append(userBubble(msg.content, msg.created_at))
    return
  }
  if (msg.role === 'system' || msg.speaker === 'moderator') {
    body.append(systemLine(msg.content))
    return
  }
  const handle = assistantBubble(msg.speaker)
  handle.text = msg.content
  handle.contentEl.textContent = msg.content
  finalizeBubble(handle, {
    status: msg.status,
    mock: msg.mock,
    error: msg.error ?? undefined,
    latencyMs: msg.latency_ms
  })
  session?.bubbles.set(`${msg.round}:${msg.speaker}`, handle)
  body.append(handle.root)
}

function userBubble(content: string, createdAt?: number): HTMLElement {
  const time = createdAt ? new Date(createdAt * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''
  return el(
    'div',
    { class: 'msg msg-user' },
    el(
      'div',
      { class: 'bubble bubble-user' },
      el('div', { class: 'bubble-text' }, content),
      time ? el('div', { class: 'bubble-time' }, time) : document.createTextNode('')
    )
  )
}

function systemLine(content: string): HTMLElement {
  return el('div', { class: 'msg msg-system' }, el('div', { class: 'system-line' }, content))
}

function assistantBubble(speaker: string): BubbleHandle {
  const color = speakerColor(speaker)
  const avatar = el('div', { class: 'avatar', style: `background:${color}` }, initials(speaker))
  const contentEl = el('div', { class: 'bubble-text' })
  const typing = el('span', { class: 'typing-dots' }, el('i'), el('i'), el('i'))
  contentEl.append(typing)
  const bubble = el('div', { class: 'bubble bubble-assistant' }, contentEl)
  const root = el(
    'div',
    { class: 'msg msg-assistant' },
    avatar,
    el(
      'div',
      { class: 'msg-body' },
      el('div', { class: 'speaker-name', style: `color:${color}` }, speaker),
      bubble
    )
  )
  return { root, contentEl, text: '', finalized: false }
}

function finalizeBubble(
  handle: BubbleHandle,
  info: { status?: string; mock?: boolean; error?: string; latencyMs?: number }
): void {
  if (handle.finalized) return
  handle.finalized = true
  // Replace typing dots / streaming text with the final text.
  clear(handle.contentEl)
  handle.contentEl.textContent = handle.text
  const footBits: string[] = []
  if (info.mock) footBits.push('mock 输出')
  if (info.latencyMs && info.latencyMs > 0) footBits.push(formatLatency(info.latencyMs))
  if (info.status && info.status !== 'ok') {
    const badge = el('span', { class: 'badge badge-error' }, info.status === 'timeout' ? '超时' : '失败')
    handle.root.querySelector('.bubble')?.append(badge)
    if (info.error) {
      handle.root.querySelector('.bubble')?.append(el('div', { class: 'bubble-error' }, info.error))
    }
    if (!handle.text) {
      handle.contentEl.textContent = info.error || '该参与者本轮未能产出回复'
      handle.root.querySelector('.bubble')?.classList.add('bubble-failed')
    }
  } else if (info.mock) {
    const badge = el('span', { class: 'badge badge-mock' }, 'mock')
    handle.root.querySelector('.bubble')?.append(badge)
  }
  if (footBits.length > 0) {
    handle.root.querySelector('.bubble')?.append(el('div', { class: 'bubble-foot' }, footBits.join(' · ')))
  }
}

function scrollToBottom(force = false): void {
  const body = $('#room-messages')
  const nearBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 120
  if (force || nearBottom) {
    body.scrollTop = body.scrollHeight
  }
}

// ==================== SSE ====================

function connectStream(roomId: string): void {
  const client = getClient()
  if (!client || !session || session.destroyed) return
  if (session.closeStream) session.closeStream()

  session.closeStream = client.streamRoom(
    roomId,
    (ev) => handleStreamEvent(ev),
    (err) => handleStreamError(err, roomId)
  )
  setStreamBanner(null)
}

function handleStreamError(err: GatewayError, roomId: string): void {
  if (!session || session.destroyed || session.roomId !== roomId) return
  if (session.streamRetries < MAX_STREAM_RETRIES) {
    session.streamRetries += 1
    const delay = 1000 * 2 ** (session.streamRetries - 1)
    setStreamBanner(`实时流断开，${Math.round(delay / 1000)}s 后自动重连（第 ${session.streamRetries} 次）…`)
    window.setTimeout(() => {
      if (session && !session.destroyed && session.roomId === roomId) connectStream(roomId)
    }, delay)
    return
  }
  setStreamBanner(`实时流已断开：${err.message}`)
  const retryBtn = document.getElementById('stream-retry')
  if (retryBtn) {
    retryBtn.addEventListener('click', () => {
      if (!session) return
      session.streamRetries = 0
      connectStream(roomId)
    })
  }
}

function setStreamBanner(message: string | null): void {
  const banner = $('#stream-banner')
  clear(banner)
  banner.classList.toggle('is-visible', message !== null)
  if (message) {
    banner.append(el('span', {}, message))
    const retry = el('button', { class: 'btn btn-ghost btn-sm', id: 'stream-retry' }, '重连')
    banner.append(retry)
  }
}

function handleStreamEvent(ev: DialogueEvent): void {
  if (!session || session.destroyed || ev.room_id !== session.roomId) return
  const body = $('#room-messages')

  switch (ev.type) {
    case 'turn_start': {
      session.turnActive = true
      setComposerEnabled(false)
      break
    }
    case 'message_start': {
      if (!ev.speaker || ev.speaker === 'user') break
      const key = `${ev.round}:${ev.speaker}`
      if (!session.bubbles.has(key)) {
        const handle = assistantBubble(ev.speaker)
        session.bubbles.set(key, handle)
        body.append(handle.root)
        scrollToBottom()
      }
      break
    }
    case 'delta': {
      if (!ev.speaker || ev.speaker === 'user' || ev.delta == null) break
      const key = `${ev.round}:${ev.speaker}`
      let handle = session.bubbles.get(key)
      if (!handle) {
        handle = assistantBubble(ev.speaker)
        session.bubbles.set(key, handle)
        body.append(handle.root)
      }
      if (!handle.finalized) {
        if (handle.text === '') {
          // First delta replaces the typing dots.
          clear(handle.contentEl)
        }
        handle.text += ev.delta
        handle.contentEl.textContent = handle.text
        scrollToBottom()
      }
      break
    }
    case 'message_end': {
      if (ev.speaker === 'user') {
        // Echo of our own optimistically rendered message — dedupe.
        if (session.pendingUserContent !== null && ev.final === session.pendingUserContent) {
          session.pendingUserContent = null
        }
        break
      }
      if (!ev.speaker) break
      if (ev.speaker === 'moderator') {
        if (ev.final) {
          body.append(systemLine(`主持人：${ev.final}`))
          scrollToBottom()
        }
        break
      }
      const key = `${ev.round}:${ev.speaker}`
      let handle = session.bubbles.get(key)
      if (!handle) {
        handle = assistantBubble(ev.speaker)
        session.bubbles.set(key, handle)
        body.append(handle.root)
      }
      if (!handle.finalized) {
        if (typeof ev.final === 'string') handle.text = ev.final
        finalizeBubble(handle, { status: ev.status, mock: ev.mock, error: ev.error ?? undefined })
        scrollToBottom()
      }
      break
    }
    case 'moderator': {
      if (ev.final) {
        body.append(systemLine(`主持人：${ev.final}`))
        scrollToBottom()
      }
      break
    }
    case 'turn_complete': {
      endTurn()
      break
    }
    default:
      // round_start / round_end carry no renderable content.
      break
  }
}

function endTurn(): void {
  if (!session) return
  session.turnActive = false
  session.pendingUserContent = null
  setComposerEnabled(true)
}

// ==================== Sending ====================

function setComposerEnabled(enabled: boolean): void {
  const input = document.getElementById('room-input') as HTMLTextAreaElement | null
  const send = document.getElementById('room-send') as HTMLButtonElement | null
  if (input) input.disabled = !enabled
  if (send) send.disabled = !enabled
}

export async function sendMessage(): Promise<void> {
  const client = getClient()
  if (!client || !session || session.turnActive) return
  const input = document.getElementById('room-input') as HTMLTextAreaElement | null
  if (!input) return
  const content = input.value.trim()
  if (!content) return

  const body = $('#room-messages')
  body.append(userBubble(content))
  scrollToBottom(true)
  session.pendingUserContent = content
  input.value = ''
  session.turnActive = true
  setComposerEnabled(false)

  const controller = new AbortController()
  session.abortPost = controller
  try {
    const resp = await client.postMessage(session.roomId, content, controller.signal)
    if (!session || session.destroyed) return
    reconcileTurn(resp.responses)
    toast(
      resp.mock_used ? `本轮完成：${resp.ok_count} 条回复（含 mock 输出）` : `本轮完成：${resp.ok_count} 条回复`,
      'success'
    )
  } catch (err) {
    if (!session || session.destroyed) return
    if (controller.signal.aborted) return // left the room mid-turn
    toast(describeError(err), 'error')
  } finally {
    if (session) {
      session.abortPost = null
      endTurn()
    }
  }
}

/** Merge the authoritative POST result into the rendered timeline. */
function reconcileTurn(responses: DialogueMessage[]): void {
  if (!session) return
  const body = $('#room-messages')
  for (const msg of responses) {
    if (msg.role === 'user') continue
    if (msg.role === 'system' || msg.speaker === 'moderator') {
      body.append(systemLine(msg.content))
      continue
    }
    const key = `${msg.round}:${msg.speaker}`
    const existing = session.bubbles.get(key)
    if (existing && existing.finalized) {
      // Keep the live-rendered bubble; only upgrade content if stream was empty.
      if (!existing.text && msg.content) {
        existing.text = msg.content
        clear(existing.contentEl)
        existing.contentEl.textContent = msg.content
      }
      continue
    }
    if (existing && !existing.finalized) {
      existing.text = msg.content
      finalizeBubble(existing, {
        status: msg.status,
        mock: msg.mock,
        error: msg.error ?? undefined,
        latencyMs: msg.latency_ms
      })
      continue
    }
    const handle = assistantBubble(msg.speaker)
    handle.text = msg.content
    clear(handle.contentEl)
    handle.contentEl.textContent = msg.content
    finalizeBubble(handle, {
      status: msg.status,
      mock: msg.mock,
      error: msg.error ?? undefined,
      latencyMs: msg.latency_ms
    })
    session.bubbles.set(key, handle)
    body.append(handle.root)
  }
  scrollToBottom(true)
}
