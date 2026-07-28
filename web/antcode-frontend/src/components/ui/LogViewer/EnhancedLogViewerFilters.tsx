import type { FC } from 'react'
import { Button, Checkbox, Col, Input, Row, Select, Space, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import { DEFAULT_LEVELS, DEFAULT_TYPES, type FilterState, type ViewerTheme } from './enhancedLogViewerTypes'

const { Search } = Input
const { Text } = Typography

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: 'default',
  INFO: 'blue',
  WARNING: 'orange',
  ERROR: 'red',
  CRITICAL: 'magenta',
}

interface FilterProps extends FilterState {
  enableSearch: boolean
  isAutoScroll: boolean
  isPaused: boolean
  onAutoScrollChange: (enabled: boolean) => void
  onLevelsChange: (levels: string[]) => void
  onPauseChange: (paused: boolean) => void
  onReset: () => void
  onSearchChange: (search: string) => void
  onTypesChange: (types: string[]) => void
  token: ViewerTheme
}

const LevelSelect: FC<Pick<FilterProps, 'selectedLevels' | 'onLevelsChange'>> = (props) => (
  <Select
    mode="multiple"
    placeholder="日志级别"
    value={props.selectedLevels}
    onChange={props.onLevelsChange}
    style={{ width: '100%' }}
    size="middle"
    maxTagCount={1}
  >
    {DEFAULT_LEVELS.map((level) => <Select.Option key={level} value={level}>
      <Tag color={LEVEL_COLORS[level]} style={{ margin: 0 }}>{level}</Tag>
    </Select.Option>)}
  </Select>
)

const TypeSelect: FC<Pick<FilterProps, 'selectedTypes' | 'onTypesChange'>> = (props) => (
  <Select
    mode="multiple"
    placeholder="日志类型"
    value={props.selectedTypes}
    onChange={props.onTypesChange}
    style={{ width: '100%' }}
    size="middle"
    maxTagCount={1}
  >
    <Select.Option value={DEFAULT_TYPES[0]}>
      <Tag color="green" style={{ margin: 0 }}>标准输出</Tag>
    </Select.Option>
    <Select.Option value={DEFAULT_TYPES[1]}>
      <Tag color="red" style={{ margin: 0 }}>标准错误</Tag>
    </Select.Option>
  </Select>
)

const FilterInputs: FC<FilterProps> = (props) => <Row gutter={[12, 12]}>
  <Col xs={24} md={12}>
    {props.enableSearch && <Search
      placeholder="搜索日志内容或来源..."
      value={props.searchText}
      onChange={(event) => props.onSearchChange(event.target.value)}
      onSearch={props.onSearchChange}
      allowClear
      size="middle"
    />}
  </Col>
  <Col xs={12} md={6}><LevelSelect {...props} /></Col>
  <Col xs={12} md={6}><TypeSelect {...props} /></Col>
  <Col xs={24} style={{ marginTop: 4 }}>
    <Space size="large">
      <Checkbox checked={props.isAutoScroll} onChange={(event) => props.onAutoScrollChange(event.target.checked)}>
        <Text style={{ fontSize: 13 }}>自动滚动</Text>
      </Checkbox>
      <Checkbox checked={props.isPaused} onChange={(event) => props.onPauseChange(event.target.checked)}>
        <Text style={{ fontSize: 13 }}>暂停接收</Text>
      </Checkbox>
    </Space>
  </Col>
</Row>

const EnhancedLogViewerFilters: FC<FilterProps> = (props) => <div style={{
  background: props.token.colorFillAlter,
  padding: '18px 20px',
  borderRadius: 6,
  border: `1px solid ${props.token.colorBorder}`,
  marginBottom: 20,
}}>
  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
    <Text strong style={{ fontSize: 14 }}>快速筛选</Text>
    <Button size="small" icon={<ReloadOutlined />} onClick={props.onReset} type="text">重置</Button>
  </div>
  <FilterInputs {...props} />
</div>

export default EnhancedLogViewerFilters
