import type React from 'react'
import { Button, Checkbox, Drawer, Form, Input, Space, Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { FileSearchOutlined, ImportOutlined } from '@ant-design/icons'
import type { GitRepository, RepositoryCandidate, RepositoryScanResult } from '@/types/repository'
import { sharedPathOptions } from '../helpers'

const { Text } = Typography

interface Props {
  open: boolean
  repository: GitRepository | null
  scanResult: RepositoryScanResult | null
  selectedSubdirs: string[]
  form: ReturnType<typeof Form.useForm>[0]
  onClose: () => void
  onScan: () => void
  onImport: () => void
  onSelectionChange: (subdirs: string[]) => void
}

const ScanImportDrawer: React.FC<Props> = ({
  open,
  repository,
  scanResult,
  selectedSubdirs,
  form,
  onClose,
  onScan,
  onImport,
  onSelectionChange,
}) => (
  <Drawer title="扫描导入" open={open} width={860} onClose={onClose} destroyOnClose>
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space style={{ justifyContent: 'space-between', width: '100%' }}>
        <Text strong>{repository?.name}</Text>
        <Button icon={<FileSearchOutlined />} onClick={onScan}>扫描仓库</Button>
      </Space>
      {scanResult && (
        <Form layout="vertical" form={form}>
          <Table
            rowKey="subdir"
            dataSource={scanResult.candidates}
            pagination={false}
            rowSelection={{
              selectedRowKeys: selectedSubdirs,
              onChange: keys => onSelectionChange(keys.map(String)),
            }}
            columns={buildCandidateColumns(scanResult, repository)}
            scroll={{ x: 760 }}
          />
          <Button
            type="primary"
            icon={<ImportOutlined />}
            onClick={onImport}
            disabled={selectedSubdirs.length === 0}
            style={{ marginTop: 16 }}
          >
            导入选中项目
          </Button>
        </Form>
      )}
    </Space>
  </Drawer>
)

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
