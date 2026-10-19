import * as api from '@/api'
import { deviceState } from '@/store/device'
import { sessionActions, sessionState } from '@/store/session'
import { useRequest } from 'ahooks'
import { Collapse } from 'antd'
import dayjs from 'dayjs'
import { useMemo } from 'react'
import { useMatch, useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'
import './nav.scss'

function formatSessionTime(value: string) {
  const time = dayjs(value)
  if (!time.isValid()) return ''
  if (time.isSame(dayjs(), 'day')) return `今天 ${time.format('HH:mm')}`
  if (time.isSame(dayjs().subtract(1, 'day'), 'day')) return `昨天 ${time.format('HH:mm')}`
  return time.format('MM/DD HH:mm')
}

export function Nav() {
  const navigate = useNavigate()
  const current = useMatch('/chat/:id')
  const activeId = current?.params.id

  const session = useSnapshot(sessionState)
  const device = useSnapshot(deviceState)

  function openSession(sessionId: string) {
    if (device.chatting) {
      window.$app.message.info('回答生成中，请稍候再切换')
      return
    }
    navigate(`/chat/${sessionId}`)
  }

  useRequest(
    async () => {
      const { data } = await api.session.list(
        {},
        {
          loading: session.list.length ? false : true,
        },
      )
      return data
    },
    {
      refreshDeps: [sessionState.updateKey],
      onSuccess(data) {
        sessionActions.setList(data?.sessions || [])
      },
    },
  )

  const items = useMemo(
    () => [
      {
        key: '1',
        label: '历史',
        children: (
          <div>
            {session.list?.length ? (
              session.list.map((item) => (
                <button
                  className={`base-layout-nav__item${item.session_id === activeId ? ' is-active' : ''}`}
                  type="button"
                  key={item.session_id}
                  aria-current={item.session_id === activeId ? 'page' : undefined}
                  title={item.session_name}
                  onClick={() => openSession(item.session_id)}
                >
                  <div className="time">{formatSessionTime(item.created_at)}</div>
                  <div className="title">{item.session_name}</div>
                </button>
              ))
            ) : (
              <div className="base-layout-nav__empty">还没有对话</div>
            )}
          </div>
        ),
      },
    ],
    [activeId, device.chatting, session.list],
  )

  return (
    <div className="base-layout-nav">
      <Collapse items={items} defaultActiveKey={['1']} />
    </div>
  )
}
