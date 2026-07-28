import type { FC } from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Button, Card, Collapse, Space, Tag } from 'antd'
import { ClearOutlined, FilterOutlined } from '@ant-design/icons'
import LogFilterAdvancedControls from './LogFilterAdvancedControls'
import LogFilterBasicControls from './LogFilterBasicControls'
import { applyLogFilter, collectFilterOptions, createLogFilter } from './logFilterUtils'
import type { LogFilter, LogSearchFilterProps } from './logSearchTypes'

export type { LogFilter } from './logSearchTypes'

const LogSearchFilter: FC<LogSearchFilterProps> = ({
  messages,
  onFilterChange,
  onFilterUpdate,
  defaultFilter = {},
  showAdvanced = true,
}) => {
  const [filter, setFilter] = useState<LogFilter>(() => createLogFilter(defaultFilter))
  const options = useMemo(() => collectFilterOptions(messages), [messages])
  const evaluation = useMemo(() => applyLogFilter(messages, filter), [filter, messages])
  const updateFilter = useCallback((updates: Partial<LogFilter>) => {
    const updated = { ...filter, ...updates }
    setFilter(updated)
    onFilterUpdate?.(updated)
  }, [filter, onFilterUpdate])
  const clearAllFilters = useCallback(() => {
    const cleared = createLogFilter()
    setFilter(cleared)
    onFilterUpdate?.(cleared)
  }, [onFilterUpdate])

  useEffect(() => {
    onFilterChange(evaluation.messages, filter)
  }, [evaluation.messages, filter, onFilterChange])

  const advancedItems = useMemo(() => [{
    key: 'advanced',
    label: '高级选项',
    children: <LogFilterAdvancedControls filter={filter} updateFilter={updateFilter} />,
  }], [filter, updateFilter])

  return <Card
    title={<Space>
      <FilterOutlined />
      <span>日志过滤器</span>
      <Tag color="blue">{evaluation.messages.length} / {messages.length}</Tag>
    </Space>}
    extra={<Button icon={<ClearOutlined />} onClick={clearAllFilters}>清除过滤器</Button>}
  >
    <Space direction="vertical" style={{ width: '100%' }}>
      <LogFilterBasicControls
        filter={filter} options={options} regexError={evaluation.regexError} updateFilter={updateFilter}
      />
      {showAdvanced && <Collapse items={advancedItems} />}
    </Space>
  </Card>
}

export default LogSearchFilter
