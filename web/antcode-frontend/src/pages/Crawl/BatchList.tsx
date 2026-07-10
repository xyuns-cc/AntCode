/**
 * 爬取批次列表页 (R1-P2-28)
 *
 * 之前前端完全没有批次管理入口——18 个后端端点全套存在但用户看不到。
 * 这里提供最小闭环：列表 / 创建 / 状态切换 / 查看聚合数据 / 导出。
 */
import type React from 'react'
import { useEffect, useState, useCallback } from 'react'
import {
  App,
  Button,
  Card,
  Space,
  Table,
  Tag,
  Popconfirm,
  Modal,
  Form,
  Input,
  InputNumber,
  Typography,
} from 'antd'
import {
  PlusOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
  DownloadOutlined,
  UnorderedListOutlined,
  StopOutlined,
  DeleteOutlined,
} from '@ant-design/icons'
import PageContainer from '@/components/common/PageContainer'
import { crawlService, type CrawlBatchSummary } from '@/services/crawl'
import Logger from '@/utils/logger'

const { Text } = Typography

const STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  paused: 'warning',
  completed: 'success',
  failed: 'error',
  cancelled: 'default',
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待启动',
  running: '运行中',
  paused: '已暂停',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

const BatchListPage: React.FC = () => {
  const { message, modal } = App.useApp()
  const [items, setItems] = useState<CrawlBatchSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(20)
  const [createOpen, setCreateOpen] = useState(false)
  const [itemsModal, setItemsModal] = useState<{ open: boolean; batch?: CrawlBatchSummary; items?: unknown[] }>({ open: false })
  const [form] = Form.useForm()

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      // listBatches 已经把后端 PaginationResponse 里的 pagination.total 拍平。
      const res = await crawlService.listBatches({ page, size })
      setItems(res.items)
      setTotal(res.total)
    } catch (e) {
      Logger.error('load crawl batches failed', e)
      message.error('加载批次失败')
    } finally {
      setLoading(false)
    }
  }, [page, size, message])

  useEffect(() => {
    refresh()
  }, [refresh])

  const doAction = async (
    action: 'start' | 'pause' | 'resume' | 'cancel' | 'delete',
    batch: CrawlBatchSummary
  ) => {
    try {
      if (action === 'start') await crawlService.startBatch(batch.id)
      else if (action === 'pause') await crawlService.pauseBatch(batch.id)
      else if (action === 'resume') await crawlService.resumeBatch(batch.id)
      else if (action === 'cancel') await crawlService.cancelBatch(batch.id)
      else await crawlService.deleteBatch(batch.id)
      message.success('操作成功')
      refresh()
    } catch (e) {
      Logger.error('batch action failed', e)
      message.error((e as Error).message || '操作失败')
    }
  }

  const openItems = async (batch: CrawlBatchSummary) => {
    try {
      const res = await crawlService.getBatchItems(batch.id, 100)
      setItemsModal({ open: true, batch, items: res.items || [] })
    } catch (e) {
      message.error('加载抓取数据失败')
    }
  }

  const download = async (batch: CrawlBatchSummary, format: 'json' | 'csv') => {
    // P2-15: 原来是 window.open(exportBatchUrl(...))，会开新标签页拿不到 axios
    // 拦截器注入的 Authorization → 登录用户导出直接 401。改走
    // crawlService.exportBatch() 用 blob + Bearer 拉字节流再本地触发下载。
    try {
      await crawlService.exportBatch(batch.id, format)
    } catch (e) {
      Logger.error('export batch failed', e)
      message.error(`导出 ${format.toUpperCase()} 失败`)
    }
  }

  const submitCreate = async () => {
    try {
      const values = await form.validateFields()
      const payload = {
        ...values,
        seed_urls: String(values.seed_urls || '')
          .split(/\r?\n/)
          .map((s: string) => s.trim())
          .filter(Boolean),
      }
      await crawlService.createBatch(payload)
      message.success('批次已创建')
      setCreateOpen(false)
      form.resetFields()
      refresh()
    } catch (e) {
      if ((e as { errorFields?: unknown[] }).errorFields) return
      Logger.error('create batch failed', e)
      message.error('创建批次失败')
    }
  }

  const columns = [
    {
      title: '批次',
      dataIndex: 'name',
      render: (name: string, r: CrawlBatchSummary) => (
        <Space direction="vertical" size={0}>
          <Text strong>{name}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {r.id?.slice(0, 12)}…
          </Text>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 110,
      render: (s: string) => <Tag color={STATUS_COLOR[s] || 'default'}>{STATUS_LABEL[s] || s}</Tag>,
    },
    {
      title: '种子',
      dataIndex: 'seed_urls',
      render: (u?: string[]) => (u ? `${u.length} URL` : '-'),
    },
    {
      title: '限制',
      render: (_: unknown, r: CrawlBatchSummary) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          深{r.max_depth ?? '-'} · 页{r.max_pages ?? '-'} · 并发{r.max_concurrency ?? '-'}
        </Text>
      ),
    },
    {
      title: '创建',
      dataIndex: 'created_at',
      width: 180,
      render: (t?: string) => (t ? new Date(t).toLocaleString() : '-'),
    },
    {
      title: '操作',
      key: 'ops',
      width: 380,
      render: (_: unknown, r: CrawlBatchSummary) => {
        const isRunning = r.status === 'running'
        const isPaused = r.status === 'paused'
        const canStart = r.status === 'pending'
        const isTerminal = ['completed', 'failed', 'cancelled'].includes(r.status)
        return (
          <Space wrap size="small">
            {canStart && (
              <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => doAction('start', r)}>启动</Button>
            )}
            {isRunning && (
              <Button size="small" icon={<PauseCircleOutlined />} onClick={() => doAction('pause', r)}>暂停</Button>
            )}
            {isPaused && (
              <Button size="small" type="primary" icon={<PlayCircleOutlined />} onClick={() => doAction('resume', r)}>恢复</Button>
            )}
            {(isRunning || isPaused) && (
              <Popconfirm title="确认取消？" onConfirm={() => doAction('cancel', r)}>
                <Button size="small" danger icon={<StopOutlined />}>取消</Button>
              </Popconfirm>
            )}
            <Button size="small" icon={<UnorderedListOutlined />} onClick={() => openItems(r)}>数据</Button>
            <Button size="small" icon={<DownloadOutlined />} onClick={() => download(r, 'json')}>JSON</Button>
            <Button size="small" icon={<DownloadOutlined />} onClick={() => download(r, 'csv')}>CSV</Button>
            {isTerminal && (
              <Popconfirm title="删除批次？" onConfirm={() => doAction('delete', r)}>
                <Button size="small" danger icon={<DeleteOutlined />} />
              </Popconfirm>
            )}
          </Space>
        )
      },
    },
  ]

  return (
    <PageContainer
      title={
        <Space>
          <span>爬取批次</span>
          <Text type="secondary" style={{ fontSize: 12 }}>
            规则爬虫批次创建 · 状态控制 · 数据导出
          </Text>
        </Space>
      }
      toolbar={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={refresh} loading={loading} size="small">刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)} size="small">新建批次</Button>
        </Space>
      }
    >
      <Card size="small" bodyStyle={{ padding: 0 }}>
        <Table<CrawlBatchSummary>
          rowKey="id"
          columns={columns}
          dataSource={items}
          loading={loading}
          pagination={{
            current: page,
            pageSize: size,
            total,
            onChange: (p, s) => {
              setPage(p)
              if (s !== size) setSize(s)
            },
          }}
        />
      </Card>

      {/* 创建批次 */}
      <Modal
        title="新建爬取批次"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={submitCreate}
        okText="创建"
        cancelText="取消"
        destroyOnHidden
      >
        <Form form={form} layout="vertical" autoComplete="off">
          <Form.Item label="项目 ID" name="project_id" rules={[{ required: true, message: '必填' }]}>
            <Input placeholder="项目 public_id" />
          </Form.Item>
          <Form.Item label="批次名称" name="name" rules={[{ required: true, message: '必填' }]}>
            <Input placeholder="例如 电商-每日-01" />
          </Form.Item>
          <Form.Item label="种子 URL（一行一个）" name="seed_urls" rules={[{ required: true, message: '至少一个' }]}>
            <Input.TextArea rows={4} placeholder="https://example.com/list/1\nhttps://example.com/list/2" />
          </Form.Item>
          <Form.Item label="描述（可选）" name="description">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Space>
            <Form.Item label="最大深度" name="max_depth" initialValue={1}>
              <InputNumber min={1} max={10} />
            </Form.Item>
            <Form.Item label="最大页数" name="max_pages" initialValue={100}>
              <InputNumber min={1} max={100000} />
            </Form.Item>
            <Form.Item label="并发" name="max_concurrency" initialValue={4}>
              <InputNumber min={1} max={64} />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      {/* 抓取数据快速预览 */}
      <Modal
        title={`抓取数据 - ${itemsModal.batch?.name || ''}`}
        open={itemsModal.open}
        onCancel={() => setItemsModal({ open: false })}
        footer={
          itemsModal.batch ? (
            <Space>
              <Button icon={<DownloadOutlined />} onClick={() => download(itemsModal.batch!, 'json')}>
                下载 JSON
              </Button>
              <Button icon={<DownloadOutlined />} onClick={() => download(itemsModal.batch!, 'csv')}>
                下载 CSV
              </Button>
              <Button onClick={() => setItemsModal({ open: false })}>关闭</Button>
            </Space>
          ) : null
        }
        width={900}
        destroyOnHidden
      >
        <Text type="secondary" style={{ fontSize: 12 }}>
          展示前 100 条；完整数据请点下载。
        </Text>
        <pre style={{ maxHeight: 480, overflow: 'auto', background: 'rgba(0,0,0,0.02)', padding: 12, marginTop: 8 }}>
          {JSON.stringify(itemsModal.items || [], null, 2)}
        </pre>
      </Modal>
    </PageContainer>
  )
}

export default BatchListPage
