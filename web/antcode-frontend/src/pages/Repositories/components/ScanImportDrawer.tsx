import type React from 'react'
import { useEffect, useState } from 'react'
import { Alert, Button, Checkbox, Drawer, Form, Input, Select, Space, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { CloudServerOutlined, FileSearchOutlined, ImportOutlined } from '@ant-design/icons'
import type { GitRepository, RepositoryCandidate, RepositoryScanResult } from '@/types/repository'
import type { Worker } from '@/types/worker'
import { workerService } from '@/services/workers'
import { sharedPathOptions } from '../helpers'

const { Text } = Typography
const PYTHON_VERSION_PATTERN = /^[0-9]+\.[0-9]+(?:\.[0-9]+)?$/

interface Props {
  open: boolean
  repository: GitRepository | null
  scanResult: RepositoryScanResult | null
  selectedSubdirs: string[]
  form: ReturnType<typeof Form.useForm>[0]
  // 本次扫描用的 ref。与仓库的 default_ref 是两件事：这个只随请求走一次，
  // 服务端不落库（repository_service.scan_for_user 只把它传给 _scan_repository）。
  scanRef: string
  onScanRefChange: (ref: string) => void
  onClose: () => void
  onScan: () => void
  onImport: () => void
  onSelectionChange: (subdirs: string[]) => void
}

const useImportWorkers = (open: boolean) => {
  const [workers, setWorkers] = useState<Worker[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    workerService.getMyAvailableWorkers({ signal: controller.signal })
      .then(items => setWorkers(items.filter(worker => worker.status === 'online')))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '加载 Worker 失败')
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [open])
  return { workers, loading, error }
}

const RuntimeFields = ({ form, workers, loading, error }: {
  form: ReturnType<typeof Form.useForm>[0]
  workers: Worker[]
  loading: boolean
  error: string | null
}) => (
  <>
    {error && <Alert type="error" showIcon message="加载可用 Worker 失败" description={error} />}
    <Space size={16} style={{ width: '100%' }} align="start">
      <Form.Item label="运行 Worker" name="worker_id" rules={[{ required: true, message: '请选择运行 Worker' }]} style={{ flex: 1 }}>
        <Select
          loading={loading}
          placeholder="选择在线 Worker"
          onChange={(workerId: string) => {
            const version = workers.find(worker => worker.id === workerId)?.pythonVersion
            form.setFieldValue('python_version', version || undefined)
          }}
          options={workers.map(worker => ({
            value: worker.id,
            label: `${worker.name}${worker.region ? ` (${worker.region})` : ''}`,
          }))}
          suffixIcon={<CloudServerOutlined />}
        />
      </Form.Item>
      <Form.Item
        label="Python 版本"
        name="python_version"
        rules={[
          { required: true, message: '请输入 Python 版本' },
          { pattern: PYTHON_VERSION_PATTERN, message: '格式应为 3.11 或 3.11.9' },
        ]}
        style={{ width: 220 }}
      >
        <Input placeholder="例如 3.11" />
      </Form.Item>
    </Space>
  </>
)

const ScanRefField = ({ repository, scanRef, onChange, onScan }: {
  repository: GitRepository | null
  scanRef: string
  onChange: (ref: string) => void
  onScan: () => void
}) => (
  <Space.Compact style={{ width: 420 }}>
    <Input
      value={scanRef}
      onChange={event => onChange(event.target.value)}
      onPressEnter={onScan}
      addonBefore="本次扫描引用"
      placeholder={repository?.default_ref}
      aria-label="本次扫描引用"
    />
    <Button icon={<FileSearchOutlined />} onClick={onScan}>扫描仓库</Button>
  </Space.Compact>
)

const ScanImportDrawer: React.FC<Props> = ({
  open,
  repository,
  scanResult,
  selectedSubdirs,
  form,
  scanRef,
  onScanRefChange,
  onClose,
  onScan,
  onImport,
  onSelectionChange,
}) => {
  const { workers, loading, error } = useImportWorkers(open)
  return (
    <Drawer title="扫描导入" open={open} width={860} onClose={onClose} destroyOnClose>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Space style={{ justifyContent: 'space-between', width: '100%' }}>
          <Text strong>{repository?.name}</Text>
          <ScanRefField
            repository={repository}
            scanRef={scanRef}
            onChange={onScanRefChange}
            onScan={onScan}
          />
        </Space>
        <Text type="secondary" style={{ fontSize: 12 }}>
          仅本次扫描生效，不会改动仓库的默认引用（{repository?.default_ref}）。要改默认值请用列表里的「编辑」。
        </Text>
        {scanResult && (
          <Form layout="vertical" form={form}>
            <RuntimeFields form={form} workers={workers} loading={loading} error={error} />
            <Table
              rowKey="subdir"
              dataSource={scanResult.candidates}
              pagination={false}
              rowSelection={{ selectedRowKeys: selectedSubdirs, onChange: keys => onSelectionChange(keys.map(String)) }}
              columns={buildCandidateColumns(scanResult, repository)}
              scroll={{ x: 760 }}
            />
            <Button type="primary" icon={<ImportOutlined />} onClick={onImport} disabled={selectedSubdirs.length === 0} style={{ marginTop: 16 }}>
              导入选中项目
            </Button>
          </Form>
        )}
      </Space>
    </Drawer>
  )
}

const buildCandidateColumns = (
  scanResult: RepositoryScanResult,
  repository: GitRepository | null,
): ColumnsType<RepositoryCandidate> => [
  { title: '子目录', dataIndex: 'subdir', width: 220, render: value => <Text code>{value}</Text> },
  { title: '入口', dataIndex: 'entry_point', width: 120, render: value => <Text code>{value}</Text> },
  {
    title: '项目名称',
    dataIndex: 'subdir',
    render: (_: string, record) => (
      <Form.Item name={['projects', record.subdir, 'name']} rules={[{ required: true }]} style={{ margin: 0 }}>
        <Input />
      </Form.Item>
    ),
  },
  {
    title: '共享目录',
    dataIndex: 'subdir',
    render: (_: string, record) => (
      <Form.Item name={['projects', record.subdir, 'include_paths']} style={{ margin: 0 }}>
        <Checkbox.Group options={sharedPathOptions(scanResult.candidates, record, repository)} />
      </Form.Item>
    ),
  },
]

export default ScanImportDrawer
