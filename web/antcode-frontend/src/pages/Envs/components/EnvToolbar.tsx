import type React from 'react'
import { Button, Input, Select, Space } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'

const { Search } = Input

interface EnvToolbarProps {
  activeTab: string
  loading: boolean
  hasCurrentWorker: boolean
  workerFilter?: string
  scopeFilter?: string
  searchQuery: string
  workerOptions: Array<{ value: string; label: string }>
  onRefresh: () => void
  onWorkerFilterChange: (value?: string) => void
  onScopeFilterChange: (value?: string) => void
  onSearchChange: (value: string) => void
  onCreate?: () => void
}

const EnvToolbar: React.FC<EnvToolbarProps> = ({
  activeTab,
  loading,
  hasCurrentWorker,
  workerFilter,
  scopeFilter,
  searchQuery,
  workerOptions,
  onRefresh,
  onWorkerFilterChange,
  onScopeFilterChange,
  onSearchChange,
  onCreate,
}) => (
  <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
    <Space size="small">
      <Button
        icon={<ReloadOutlined />}
        onClick={onRefresh}
        loading={loading}
        size="small"
      >
        刷新
      </Button>
      {onCreate && (
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={onCreate}
          size="small"
        >
          新建环境
        </Button>
      )}
    </Space>
    {activeTab === 'envs' && (
      <Space wrap size="small">
        {!hasCurrentWorker && (
          <Select
            allowClear
            placeholder="Worker"
            style={{ width: 160 }}
            value={workerFilter}
            onChange={onWorkerFilterChange}
            options={workerOptions}
            size="small"
          />
        )}
        <Select
          allowClear
          placeholder="作用域"
          style={{ width: 90 }}
          value={scopeFilter}
          onChange={onScopeFilterChange}
          options={[
            { value: 'private', label: '私有' },
            { value: 'shared', label: '公共' },
          ]}
          size="small"
        />
        <Search
          placeholder={hasCurrentWorker ? '搜索路径/名称/版本' : '搜索路径/名称/版本/Worker'}
          value={searchQuery}
          onChange={(event) => onSearchChange(event.target.value)}
          onSearch={() => onSearchChange(searchQuery)}
          allowClear
          style={{ width: 240 }}
          size="small"
        />
      </Space>
    )}
  </Space>
)

export default EnvToolbar
