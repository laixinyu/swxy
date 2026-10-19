import * as api from '@/api'
import { FilterOutlined } from '@ant-design/icons'
import { useRequest } from 'ahooks'
import { Button, Checkbox, Empty, Popover, Tag } from 'antd'
import { useEffect, useMemo, useState } from 'react'

const STATUS_TEXT: Record<API.RepositoryStatus, string> = {
  pending: '排队中',
  parsing: '解析中',
  done: '已完成',
  failed: '异常',
}

/** 检索范围：只在选中的文档里找答案。不选表示全部文档。保存在会话上，刷新后仍然有效。 */
export default function Scope(props: { sessionId: string }) {
  const { sessionId } = props
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<string[]>([])

  const files = useRequest(
    async () => (await api.repository.list(undefined, { loading: false })).data ?? [],
    { refreshDeps: [open] },
  )
  const scope = useRequest(
    async () => (await api.session.getScope({ session_id: sessionId }, { loading: false })).data,
    { refreshDeps: [sessionId] },
  )
  const saving = useRequest(
    async (doc_ids: string[]) =>
      (await api.session.updateScope({ session_id: sessionId, doc_ids })).data,
    {
      manual: true,
      onSuccess(data) {
        scope.mutate(data)
        setOpen(false)
      },
    },
  )

  useEffect(() => {
    if (open) setDraft(scope.data?.doc_ids ?? [])
  }, [open, scope.data])

  const notReady = useMemo(
    () => files.data?.filter((f) => f.status === 'pending' || f.status === 'parsing').length ?? 0,
    [files.data],
  )
  const selected = scope.data?.doc_ids.length ?? 0
  const label = selected ? `${selected} 个文档` : '全部文档'

  const content = (
    <div style={{ width: 320 }}>
      {notReady > 0 && (
        <div style={{ color: '#E6A23C', fontSize: 12, marginBottom: 8 }}>
          有 {notReady} 个文档还在解析，完成之前检索不到它们的内容。
        </div>
      )}
      {files.data?.length ? (
        <Checkbox.Group
          style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 280, overflow: 'auto' }}
          value={draft}
          onChange={(value) => setDraft(value as string[])}
        >
          {files.data.map((file) => (
            <Checkbox key={file.id} value={file.id} disabled={file.status !== 'done'}>
              <span title={file.file_name}>{file.file_name}</span>
              {file.status !== 'done' && (
                <Tag style={{ marginLeft: 6 }} bordered={false}>
                  {STATUS_TEXT[file.status]}
                </Tag>
              )}
            </Checkbox>
          ))}
        </Checkbox.Group>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="知识库里还没有文档" />
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12 }}>
        <Button size="small" onClick={() => saving.run([])} loading={saving.loading}>
          检索全部文档
        </Button>
        <Button
          size="small"
          type="primary"
          disabled={draft.length === 0}
          loading={saving.loading}
          onClick={() => saving.run(draft)}
        >
          只在选中的 {draft.length} 个文档里检索
        </Button>
      </div>
    </div>
  )

  return (
    <Popover
      trigger="click"
      placement="topLeft"
      title="检索范围"
      content={content}
      open={open}
      onOpenChange={setOpen}
    >
      <Button
        className="com-sender__action--contract"
        variant={selected ? 'filled' : 'text'}
        color={selected ? 'primary' : 'default'}
        shape="round"
        title={scope.data?.documents.map((d) => d.file_name).join('、') || '在全部文档里检索'}
      >
        <FilterOutlined style={{ fontSize: 14 }} />
        {label}
        {notReady > 0 && <span style={{ color: '#E6A23C' }}>（{notReady} 个解析中）</span>}
      </Button>
    </Popover>
  )
}
