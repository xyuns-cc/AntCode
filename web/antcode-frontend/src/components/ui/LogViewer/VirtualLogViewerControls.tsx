import type { FC, RefObject } from 'react'
import { Button, Input, Space, Tag } from 'antd'
import { ClearOutlined } from '@ant-design/icons'
import LogSearchFilter from './LogSearchFilter'
import type { LogFilter } from './logSearchTypes'
import type { LogMessage, ViewerTheme } from './enhancedLogViewerTypes'
import type { VirtualizedListRef, VirtualLogStats } from './virtualLogTypes'

const { Search } = Input

interface HeaderProps {
  listRef: RefObject<VirtualizedListRef>
  onClear?: () => void
  showFiltered: boolean
  stats: VirtualLogStats
}

export const VirtualLogViewerTitle: FC<Pick<HeaderProps, 'showFiltered' | 'stats'>> = ({ showFiltered, stats }) => <Space>
    <span>虚拟日志查看器</span>
    <Tag color="blue">总计: {stats.total}</Tag>
    <Tag color="green">正常: {stats.stdout}</Tag>
    <Tag color="red">错误: {stats.stderr}</Tag>
    {showFiltered && <Tag color="orange">已过滤: {stats.filtered}</Tag>}
  </Space>

export const VirtualLogViewerActions: FC<Pick<HeaderProps, 'listRef' | 'onClear'>> = ({ listRef, onClear }) => <Space>
  {onClear && <Button icon={<ClearOutlined />} onClick={onClear} size="small">清除</Button>}
  <Button onClick={() => listRef.current?.scrollToBottom()} size="small">滚动到底部</Button>
</Space>

interface SearchProps {
  advanced: boolean
  filterKey: string
  messages: LogMessage[]
  onAdvancedChange: (messages: LogMessage[], filter: LogFilter) => void
  onSearchChange: (value: string) => void
  searchText: string
}

export const VirtualLogViewerSearch: FC<SearchProps> = (props) => <div style={{ marginBottom: 12 }}>
  {props.advanced
    ? <LogSearchFilter
      key={props.filterKey} messages={props.messages} onFilterChange={props.onAdvancedChange} showAdvanced
    />
    : <Search
      placeholder="搜索日志内容、来源或级别..."
      value={props.searchText}
      onChange={(event) => props.onSearchChange(event.target.value)}
      onSearch={props.onSearchChange}
      allowClear
      size="small"
      suffix={props.searchText && <Button
        aria-label="清除搜索"
        type="text" icon={<ClearOutlined />} onClick={() => props.onSearchChange('')} size="small"
      />}
    />}
</div>

interface FooterProps {
  searchText: string
  stats: VirtualLogStats
  token: ViewerTheme
}

export const VirtualLogViewerFooter: FC<FooterProps> = ({ searchText, stats, token }) => <div style={{
  marginTop: 8,
  padding: '4px 0',
  fontSize: '12px',
  color: token.colorTextSecondary,
  borderTop: `1px solid ${token.colorBorderSecondary}`,
}}>
  显示 {stats.filtered} / {stats.total} 条日志
  {searchText && ` (搜索: "${searchText}")`}
  {stats.errors > 0 && <span style={{ color: token.colorError, marginLeft: 16 }}>错误: {stats.errors}</span>}
  {stats.warnings > 0 && <span style={{ color: token.colorWarning, marginLeft: 8 }}>警告: {stats.warnings}</span>}
</div>
