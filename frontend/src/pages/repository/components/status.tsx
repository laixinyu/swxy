import { Tag, Tooltip } from 'antd'
import Color from 'color'
import { useMemo } from 'react'

const map: Record<API.RepositoryStatus, { text: string; color: string }> = {
  pending: {
    text: '排队中',
    color: '#909399',
  },
  parsing: {
    text: '解析中',
    color: '#E6A23C',
  },
  done: {
    text: '已完成',
    color: '#409EFF',
  },
  failed: {
    text: '异常',
    color: '#F56C6C',
  },
}

export function Status(props: {
  status: API.RepositoryStatus
  error?: string | null
  progress?: number
  progressMsg?: string | null
}) {
  const { status, error, progress, progressMsg } = props
  const { text, color } = useMemo(() => {
    return (
      map[status] ?? {
        color: '#999',
        text: status,
      }
    )
  }, [status])
  const backgroundColor = useMemo(() => {
    return new Color(color).alpha(0.1).toString()
  }, [color])
  const borderColor = useMemo(() => {
    return new Color(color).alpha(0.3).toString()
  }, [color])

  const percent = status === 'parsing' && progress ? ` ${Math.round(progress * 100)}%` : ''
  const tag = (
    <Tag style={{ borderColor, color, backgroundColor }}>
      {text}
      {percent}
    </Tag>
  )
  // 已完成但带错误信息：重新解析失败，仍在使用上次的结果
  const tip = status === 'parsing' ? progressMsg : error
  if (!tip) return tag
  return (
    <Tooltip title={tip}>
      <span>
        {tag}
        {status === 'done' && <span style={{ color: '#E6A23C' }}>⚠</span>}
      </span>
    </Tooltip>
  )
}
