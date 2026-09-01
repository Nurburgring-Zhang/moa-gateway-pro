/**
 * Dialogue page — multi-AI room list + room creation.
 *
 * Data source: GET /v1/dialogue/rooms. Room creation needs real model-pool
 * endpoint ids, so the create sheet loads GET /v1/models and offers only the
 * real enabled endpoints (entries owned by "moa-gateway" are routing presets,
 * not dialogue participants — the engine validates endpoint_id against the
 * model pool and rejects anything else with 404).
 */

import { GatewayError } from '../gateway'
import { getClient } from '../state'
import type { CreateRoomRequest, DialogueMode, DialogueRoom, ModelInfo } from '../types'
import { $, clear, confirmSheet, el, formatTime, MODE_LABELS, openSheet, toast } from '../ui'
import { openRoom } from './room'

export async function renderDialogue(): Promise<void> {
  const page = $('#page-dialogue')
  clear(page)
  page.append(
    el(
      'div',
      { class: 'page-header' },
      el('h2', { class: 'page-title' }, '多 AI 对话'),
      (() => {
        const btn = el('button', { class: 'btn btn-primary btn-sm', id: 'btn-new-room' }, '+ 新建房间')
        btn.addEventListener('click', () => void openCreateRoomSheet())
        return btn
      })()
    )
  )

  const listWrap = el('div', { class: 'room-list', id: 'room-list' })
  page.append(listWrap)
  await loadRooms()
}

export async function loadRooms(): Promise<void> {
  const listWrap = document.getElementById('room-list')
  if (!listWrap) return
  const client = getClient()
  if (!client) {
    clear(listWrap)
    listWrap.append(
      el(
        'div',
        { class: 'empty-state' },
        el('div', { class: 'empty-icon' }, '⚠'),
        el('p', { class: 'empty-text' }, '尚未配置网关，请先到「设置」填写网关地址。')
      )
    )
    return
  }

  clear(listWrap)
  listWrap.append(el('div', { class: 'list-loading' }, '加载房间列表…'))

  try {
    const resp = await client.listRooms()
    clear(listWrap)
    if (resp.rooms.length === 0) {
      listWrap.append(
        el(
          'div',
          { class: 'empty-state' },
          el('div', { class: 'empty-icon' }, '💬'),
          el('p', { class: 'empty-text' }, '还没有对话房间。'),
          el('p', { class: 'empty-hint' }, '点右上角「新建房间」，选择至少 2 个 AI 端点开始多 AI 同框对话。')
        )
      )
      return
    }
    // Most recently updated first.
    const rooms = [...resp.rooms].sort((a, b) => b.updated_at - a.updated_at)
    for (const room of rooms) {
      listWrap.append(roomCard(room))
    }
  } catch (err) {
    clear(listWrap)
    listWrap.append(
      el(
        'div',
        { class: 'empty-state' },
        el('div', { class: 'empty-icon' }, '✕'),
        el('p', { class: 'empty-text' }, describeError(err)),
        (() => {
          const retry = el('button', { class: 'btn btn-secondary btn-sm' }, '重试')
          retry.addEventListener('click', () => void loadRooms())
          return retry
        })()
      )
    )
  }
}

function roomCard(room: DialogueRoom): HTMLElement {
  const names = room.participants.map((p) => p.name).join(' · ')
  const card = el(
    'div',
    { class: 'room-card card', role: 'button', tabindex: '0' },
    el(
      'div',
      { class: 'room-card-top' },
      el('div', { class: 'room-topic' }, room.topic),
      el('span', { class: `badge badge-mode-${room.mode}` }, MODE_LABELS[room.mode] ?? room.mode)
    ),
    el('div', { class: 'room-participants' }, names),
    el(
      'div',
      { class: 'room-meta' },
      el('span', {}, `${room.participants.length} 位参与者`),
      el('span', { class: 'dot-sep' }, '·'),
      el('span', {}, `第 ${room.current_round} 轮`),
      el('span', { class: 'dot-sep' }, '·'),
      el('span', {}, formatTime(room.updated_at)),
      room.status === 'archived' ? el('span', { class: 'badge badge-archived' }, '已归档') : document.createTextNode('')
    )
  )
  card.addEventListener('click', () => openRoom(room.room_id))

  // Long-press / context menu → delete.
  let pressTimer: number | undefined
  card.addEventListener('contextmenu', (e) => {
    e.preventDefault()
    confirmDeleteRoom(room)
  })
  card.addEventListener('touchstart', () => {
    pressTimer = window.setTimeout(() => confirmDeleteRoom(room), 600)
  }, { passive: true })
  const cancelPress = (): void => {
    if (pressTimer) window.clearTimeout(pressTimer)
    pressTimer = undefined
  }
  card.addEventListener('touchend', cancelPress)
  card.addEventListener('touchmove', cancelPress)

  // A visible affordance too (discoverability beats hidden gestures).
  const del = el('button', { class: 'room-delete', 'aria-label': '删除房间' }, '🗑')
  del.addEventListener('click', (e) => {
    e.stopPropagation()
    confirmDeleteRoom(room)
  })
  card.querySelector('.room-card-top')?.append(del)
  return card
}

function confirmDeleteRoom(room: DialogueRoom): void {
  confirmSheet(
    '删除对话房间',
    `将永久删除「${room.topic}」及其全部消息记录，无法恢复。`,
    '删除',
    () => void deleteRoom(room.room_id)
  )
}

async function deleteRoom(roomId: string): Promise<void> {
  const client = getClient()
  if (!client) return
  try {
    await client.deleteRoom(roomId)
    toast('房间已删除', 'success')
    await loadRooms()
  } catch (err) {
    toast(describeError(err), 'error')
  }
}

// ==================== Create room ====================

interface ParticipantDraft {
  endpoint_id: string
  name: string
  persona: string
}

async function openCreateRoomSheet(): Promise<void> {
  const client = getClient()
  if (!client) {
    toast('请先配置网关', 'error')
    return
  }

  let endpoints: ModelInfo[] = []
  try {
    const models = await client.listModels()
    // Presets are owned by "moa-gateway"; real endpoints carry their provider.
    endpoints = models.data.filter((m) => m.owned_by !== 'moa-gateway')
  } catch (err) {
    toast(`加载模型列表失败：${describeError(err)}`, 'error')
    return
  }
  if (endpoints.length === 0) {
    toast('网关没有可用的真实模型端点（enabled 的端点才会出现在列表里）', 'error')
    return
  }

  const selected = new Map<string, ParticipantDraft>()
  let mode: DialogueMode = 'round_robin'

  openSheet((close) => {
    const body = el('div', { class: 'sheet-body' })
    body.append(el('div', { class: 'sheet-handle' }))
    body.append(el('h3', { class: 'sheet-title' }, '新建多 AI 对话房间'))

    // Topic
    const topicInput = el('input', {
      class: 'input',
      id: 'room-topic',
      type: 'text',
      placeholder: '对话主题，例如：评估微服务拆分方案',
      maxlength: '200'
    }) as HTMLInputElement
    body.append(el('label', { class: 'field-label' }, '主题'), topicInput)

    // Mode segmented control
    const modes: Array<{ value: DialogueMode; label: string; hint: string }> = [
      { value: 'round_robin', label: '轮流发言', hint: '按顺序逐个发言，能看到彼此观点' },
      { value: 'parallel_think', label: '并行思考', hint: '同轮独立思考后汇总' },
      { value: 'free_talk', label: '自由讨论', hint: '主持人 LLM 决定发言顺序' }
    ]
    const modeWrap = el('div', { class: 'mode-list' })
    body.append(el('label', { class: 'field-label' }, '编排模式'), modeWrap)
    const modeButtons = new Map<DialogueMode, HTMLElement>()
    for (const m of modes) {
      const btn = el(
        'button',
        { class: `mode-option ${m.value === mode ? 'is-selected' : ''}`, type: 'button' },
        el('div', { class: 'mode-option-label' }, m.label),
        el('div', { class: 'mode-option-hint' }, m.hint)
      )
      btn.addEventListener('click', () => {
        mode = m.value
        for (const [value, node] of modeButtons) {
          node.classList.toggle('is-selected', value === mode)
        }
      })
      modeButtons.set(m.value, btn)
      modeWrap.append(btn)
    }

    // Participants
    body.append(
      el('label', { class: 'field-label' }, `选择参与者（至少 2 个，已选 ${selected.size}）`)
    )
    const countLabel = body.lastElementChild as HTMLElement
    const endpointList = el('div', { class: 'endpoint-list' })
    body.append(endpointList)

    const renderEndpointList = (): void => {
      clear(endpointList)
      for (const ep of endpoints) {
        const isSelected = selected.has(ep.id)
        const row = el(
          'div',
          { class: `endpoint-row ${isSelected ? 'is-selected' : ''}` },
          el(
            'div',
            { class: 'endpoint-check' },
            el('div', { class: `checkbox ${isSelected ? 'is-checked' : ''}` }, isSelected ? '✓' : '')
          ),
          el(
            'div',
            { class: 'endpoint-info' },
            el('div', { class: 'endpoint-id' }, ep.id),
            el('div', { class: 'endpoint-desc' }, ep.description ?? ep.owned_by)
          )
        )
        row.addEventListener('click', () => {
          if (selected.has(ep.id)) {
            selected.delete(ep.id)
          } else {
            selected.set(ep.id, { endpoint_id: ep.id, name: ep.id, persona: '' })
          }
          countLabel.textContent = `选择参与者（至少 2 个，已选 ${selected.size}）`
          renderEndpointList()
        })
        endpointList.append(row)
      }
      // Selected participants get name/persona editors below the list.
      if (selected.size > 0) {
        const editors = el('div', { class: 'participant-editors' })
        for (const draft of selected.values()) {
          const nameInput = el('input', {
            class: 'input input-sm',
            type: 'text',
            placeholder: '显示名',
            maxlength: '40'
          }) as HTMLInputElement
          nameInput.value = draft.name
          nameInput.addEventListener('input', () => {
            draft.name = nameInput.value.trim() || draft.endpoint_id
          })
          const personaInput = el('input', {
            class: 'input input-sm',
            type: 'text',
            placeholder: '人设（可选），例如：资深架构师，关注成本',
            maxlength: '200'
          }) as HTMLInputElement
          personaInput.value = draft.persona
          personaInput.addEventListener('input', () => {
            draft.persona = personaInput.value
          })
          editors.append(
            el(
              'div',
              { class: 'participant-editor' },
              el('div', { class: 'participant-editor-id' }, draft.endpoint_id),
              nameInput,
              personaInput
            )
          )
        }
        endpointList.append(editors)
      }
    }
    renderEndpointList()

    // Actions
    const submitBtn = el('button', { class: 'btn btn-primary btn-block btn-lg' }, '创建房间')
    let submitting = false
    submitBtn.addEventListener('click', async () => {
      if (submitting) return
      const topic = topicInput.value.trim()
      if (!topic) {
        toast('请填写对话主题', 'error')
        topicInput.focus()
        return
      }
      if (selected.size < 2) {
        toast('至少选择 2 个参与者', 'error')
        return
      }
      const participants: CreateRoomRequest['participants'] = []
      for (const draft of selected.values()) {
        participants.push({
          endpoint_id: draft.endpoint_id,
          name: draft.name.trim() || draft.endpoint_id,
          persona: draft.persona.trim()
        })
      }
      const names = new Set(participants.map((p) => p.name))
      if (names.size !== participants.length) {
        toast('参与者显示名不能重复', 'error')
        return
      }
      submitting = true
      submitBtn.disabled = true
      submitBtn.textContent = '创建中…'
      try {
        const room = await client.createRoom({
          topic,
          mode,
          participants,
          max_rounds: 2,
          participant_timeout: 60
        })
        toast('房间已创建', 'success')
        close()
        await loadRooms()
        openRoom(room.room_id)
      } catch (err) {
        toast(describeError(err), 'error')
        submitting = false
        submitBtn.disabled = false
        submitBtn.textContent = '创建房间'
      }
    })
    body.append(el('div', { class: 'sheet-actions' }, submitBtn))
    return body
  })
}

export function describeError(err: unknown): string {
  if (err instanceof GatewayError) {
    if (err.status === 401) return 'API Key 无效或未提供（401）'
    if (err.status === 403) return '没有权限（403）'
    if (err.status === 404) return '资源不存在（404）'
    if (err.status === 409) return `房间状态冲突：${err.message}`
    if (err.status === 422) return `参数校验失败：${err.message}`
    if (err.status === 429) return '触发网关限流（429），请稍后再试'
    if (err.status === 0) return err.message
    return `网关错误（HTTP ${err.status}）：${err.message}`
  }
  return err instanceof Error ? err.message : String(err)
}
