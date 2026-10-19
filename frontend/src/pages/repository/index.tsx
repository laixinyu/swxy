import * as api from '@/api'
import IconDelete from '@/assets/repository/action/delete.svg'
import IconSearch from '@/assets/repository/search.svg'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { useRequest } from 'ahooks'
import { Button, Input, Modal, Popconfirm, Space, Table } from 'antd'
import { ColumnsType } from 'antd/es/table'
import { TableRowSelection } from 'antd/es/table/interface'
import dayjs from 'dayjs'
import { useEffect, useMemo, useRef, useState } from 'react'
import { FileIcon } from './components/file-icon'
import { Status } from './components/status'
import RepositoryUpload, { RepositoryUploadRef } from './components/upload'
import styles from './index.module.scss'

type IRepository = API.Repository & {
  $suffix: FileIcon
  method: string
}

const POLL_INTERVAL = 3000

export default function Index() {
  const { data, refresh } = useRequest(async () => {
    const { data } = await api.repository.list(undefined, { loading: false })
    return data?.map(
      (item) =>
        ({
          ...item,
          $suffix: item.file_name.split('.').pop() as FileIcon,
          method: 'General',
        }) satisfies IRepository,
    )
  })

  // 有文档还在排队或解析时定时刷新，全部完成后停止
  const busy = useMemo(
    () => data?.some((item) => item.status === 'pending' || item.status === 'parsing') ?? false,
    [data],
  )
  useEffect(() => {
    if (!busy) return
    const timer = window.setTimeout(refresh, POLL_INTERVAL)
    return () => window.clearTimeout(timer)
  }, [busy, data, refresh])

  const columns = useMemo<ColumnsType<IRepository>>(
    () => [
      {
        title: '文件名',
        dataIndex: 'file_name',
        width: 200,
        render(value, row) {
          return (
            <div className={styles['repository-page__file-name']} title={value}>
              <FileIcon className={styles['icon']} suffix={row.$suffix} />
              {value}
            </div>
          )
        },
      },
      {
        title: '切片数量',
        dataIndex: 'chunk_count',
        width: 100,
      },
      {
        title: '更新时间',
        dataIndex: 'updated_at',
        width: 200,
        render(value) {
          return dayjs(value).format('MM/DD/YYYY HH:mm:ss')
        },
      },
      {
        title: '切片方式',
        dataIndex: 'method',
        width: 100,
      },
      {
        title: '状态',
        dataIndex: 'status',
        width: 100,
        render(value, row) {
          return (
            <Status
              status={value}
              error={row.error}
              progress={row.progress}
              progressMsg={row.progress_msg}
            />
          )
        },
      },
      {
        title: '操作',
        dataIndex: 'action',
        width: 100,
        render(_, row) {
          return (
            <Space>
              {(row.status === 'failed' || row.status === 'done') && (
                <Popconfirm
                  title={row.status === 'failed' ? '重新解析这个文档？' : '重新解析这个文档？完成之前仍然使用现有的切片。'}
                  onConfirm={async () => {
                    await api.repository.reparse({ id: row.id })
                    refresh()
                  }}
                >
                  <Button
                    color="default"
                    variant="text"
                    shape="circle"
                    size="small"
                    title="重新解析"
                  >
                    <ReloadOutlined />
                  </Button>
                </Popconfirm>
              )}
              <Popconfirm
                title="确定要删除该文件吗？"
                onConfirm={async () => {
                  await api.repository.remove({ file_name: row.file_name })
                  refresh()
                }}
              >
                <Button
                  color="default"
                  variant="text"
                  shape="circle"
                  size="small"
                >
                  <img src={IconDelete} />
                </Button>
              </Popconfirm>
            </Space>
          )
        },
      },
    ],
    [],
  )
  const scroll = useMemo(() => {
    return {
      x: columns?.reduce((prev, current) => {
        return prev + parseInt(String(current.width ?? 0))
      }, 0),
    }
  }, [columns])

  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])

  const onSelectChange = (newSelectedRowKeys: React.Key[]) => {
    setSelectedRowKeys(newSelectedRowKeys)
  }
  const rowSelection: TableRowSelection<IRepository> = {
    selectedRowKeys,
    onChange: onSelectChange,
  }

  /* 上传 */
  const [openUpload, setOpenUpload] = useState(false)
  const uploadRef = useRef<RepositoryUploadRef>(null)
  const [uploading, setUploading] = useState(false)

  return (
    <div className={styles['repository-page']}>
      <div className={styles['repository-page__header']}>
        <div className={styles['title']}>知识库</div>
        <div className={styles['desc']}>
          在开始AI对话之前，请等待文档解析完成。
        </div>
      </div>

      <div className={styles['repository-page__body']}>
        <div className={styles['header']}>
          <Input
            placeholder="搜索文件"
            prefix={<img src={IconSearch} />}
            style={{ width: 210 }}
          />

          <Button type="primary" onClick={() => setOpenUpload(true)}>
            <PlusOutlined />
            添加文档
          </Button>
        </div>

        <Table<IRepository>
          rowKey="id"
          columns={columns}
          dataSource={data}
          rowSelection={rowSelection}
          scroll={scroll}
          pagination={false}
        />
      </div>

      <Modal
        title="上传文档"
        open={openUpload}
        width={400}
        destroyOnClose
        onCancel={() => {
          if (uploading) return
          setOpenUpload(false)
        }}
        onOk={async () => {
          setUploading(true)
          try {
            await uploadRef.current?.submit()
            setOpenUpload(false)
            refresh()
          } finally {
            setUploading(false)
          }
        }}
      >
        <RepositoryUpload beforeUpload={() => false} ref={uploadRef} />
      </Modal>
    </div>
  )
}
