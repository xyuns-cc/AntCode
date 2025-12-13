import React, { useState, useCallback, useMemo } from 'react'
import {
  Card,
  Input,
  Select,
  DatePicker,
  Button,
  Space,
  Row,
  Col,
  Tag,
  Collapse,
  Switch,
  Slider,
  Tooltip
} from 'antd'
import {
  FilterOutlined,
  ClearOutlined,
  EyeOutlined,
  EyeInvisibleOutlined
} from '@ant-design/icons'
import type { Dayjs } from 'dayjs'

const { Search } = Input
const { Option } = Select
const { RangePicker } = DatePicker

// 日志过滤器接口
export interface LogFilter {
  searchText?: string
  logTypes?: string[]
  levels?: string[]
  sources?: string[]
  timeRange?: [Dayjs, Dayjs]
  maxLines?: number
  caseSensitive?: boolean
  useRegex?: boolean
  showTimestamp?: boolean
  showLevel?: boolean
  showSource?: boolean
}

// 日志消息接口
interface LogMessage {
  id: string
  type: 'stdout' | 'stderr' | 'info' | 'error' | 'warning' | 'success'
  content: string
  timestamp: string
  level?: string
  source?: string
}

// 组件属性
interface LogSearchFilterProps {
  messages: LogMessage[]
  onFilterChange: (filteredMessages: LogMessage[], filter: LogFilter) => void
  onFilterUpdate?: (filter: LogFilter) => void
  defaultFilter?: Partial<LogFilter>
  showAdvanced?: boolean
}

const LogSearchFilter: React.FC<LogSearchFilterProps> = ({
  messages,
  onFilterChange,
  onFilterUpdate,
  defaultFilter = {},
  showAdvanced = true
}) => {
  // 过滤器状态
  const [filter, setFilter] = useState<LogFilter>({
    searchText: '',
    logTypes: [],
    levels: [],
    sources: [],
    maxLines: 1000,
    caseSensitive: false,
    useRegex: false,
    showTimestamp: true,
    showLevel: true,
    showSource: true,
    ...defaultFilter
  })

  // 可用的选项
  const availableOptions = useMemo(() => {
    const types = new Set<string>()
    const levels = new Set<string>()
    const sources = new Set<string>()

    messages.forEach(msg => {
      types.add(msg.type)
      if (msg.level) levels.add(msg.level)
      if (msg.source) sources.add(msg.source)
    })

    return {
      types: Array.from(types).sort(),
      levels: Array.from(levels).sort(),
      sources: Array.from(sources).sort()
    }
  }, [messages])

  // 过滤消息
  const filteredMessages = useMemo(() => {
    let filtered = messages

    // 文本搜索
    if (filter.searchText) {
      const searchText = filter.caseSensitive 
        ? filter.searchText 
        : filter.searchText.toLowerCase()

      if (filter.useRegex) {
        try {
          const regex = new RegExp(searchText, filter.caseSensitive ? 'g' : 'gi')
          filtered = filtered.filter(msg => 
            regex.test(msg.content) ||
            (msg.source && regex.test(msg.source)) ||
            (msg.level && regex.test(msg.level))
          )
        } catch {
          // 正则表达式无效，使用普通搜索
          filtered = filtered.filter(msg => {
            const content = filter.caseSensitive ? msg.content : msg.content.toLowerCase()
            const source = msg.source ? (filter.caseSensitive ? msg.source : msg.source.toLowerCase()) : ''
            const level = msg.level ? (filter.caseSensitive ? msg.level : msg.level.toLowerCase()) : ''
            
            return content.includes(searchText) || source.includes(searchText) || level.includes(searchText)
          })
        }
      } else {
        filtered = filtered.filter(msg => {
          const content = filter.caseSensitive ? msg.content : msg.content.toLowerCase()
          const source = msg.source ? (filter.caseSensitive ? msg.source : msg.source.toLowerCase()) : ''
          const level = msg.level ? (filter.caseSensitive ? msg.level : msg.level.toLowerCase()) : ''
          
          return content.includes(searchText) || source.includes(searchText) || level.includes(searchText)
        })
      }
    }

    // 日志类型过滤
    if (filter.logTypes && filter.logTypes.length > 0) {
      filtered = filtered.filter(msg => filter.logTypes!.includes(msg.type))
    }

    // 日志级别过滤
    if (filter.levels && filter.levels.length > 0) {
      filtered = filtered.filter(msg => msg.level && filter.levels!.includes(msg.level))
    }

    // 日志源过滤
    if (filter.sources && filter.sources.length > 0) {
      filtered = filtered.filter(msg => msg.source && filter.sources!.includes(msg.source))
    }

    // 时间范围过滤
    if (filter.timeRange && filter.timeRange.length === 2) {
      const [start, end] = filter.timeRange
      filtered = filtered.filter(msg => {
        const msgTime = new Date(msg.timestamp)
        return msgTime >= start.toDate() && msgTime <= end.toDate()
      })
    }

    // 限制行数
    if (filter.maxLines && filtered.length > filter.maxLines) {
      filtered = filtered.slice(-filter.maxLines)
    }

    return filtered
  }, [messages, filter])

  // 更新过滤器
  const updateFilter = useCallback((updates: Partial<LogFilter>) => {
    const newFilter = { ...filter, ...updates }
    setFilter(newFilter)
    onFilterUpdate?.(newFilter)
  }, [filter, onFilterUpdate])

  // 通知过滤结果变化
  React.useEffect(() => {
    onFilterChange(filteredMessages, filter)
  }, [filteredMessages, filter, onFilterChange])

  // 清除所有过滤器
  const clearAllFilters = () => {
    const clearedFilter: LogFilter = {
      searchText: '',
      logTypes: [],
      levels: [],
      sources: [],
      maxLines: 1000,
      caseSensitive: false,
      useRegex: false,
      showTimestamp: true,
      showLevel: true,
      showSource: true
    }
    setFilter(clearedFilter)
    onFilterUpdate?.(clearedFilter)
  }

  // 获取类型标签颜色和显示文本
  const getTypeInfo = (type: string) => {
    switch (type) {
      case 'stdout': return { color: 'green', text: '标准输出' }
      case 'stderr': return { color: 'red', text: '标准错误' }
      case 'error': return { color: 'red', text: '错误' }
      case 'warning': return { color: 'orange', text: '警告' }
      case 'info': return { color: 'blue', text: '信息' }
      case 'success': return { color: 'green', text: '成功' }
      default: return { color: 'default', text: type.toUpperCase() }
    }
  }

  // 获取级别标签颜色
  const getLevelColor = (level: string) => {
    if (!level) return 'default'
    switch (level.toUpperCase()) {
      case 'DEBUG': return 'default'
      case 'INFO': return 'blue'
      case 'WARNING': return 'orange'
      case 'ERROR': return 'red'
      case 'CRITICAL': return 'magenta'
      default: return 'default'
    }
  }

  return (
    <Card
      title={
        <Space>
          <FilterOutlined />
          <span>日志过滤器</span>
          <Tag color="blue">
            {filteredMessages.length} / {messages.length}
          </Tag>
        </Space>
      }
      extra={
        <Button
          icon={<ClearOutlined />}
          onClick={clearAllFilters}
          size="small"
        >
          清除过滤器
        </Button>
      }
      size="small"
    >
      <Space direction="vertical" style={{ width: '100%' }}>
        {/* 基础搜索 - 调整为两行布局以给选择器更多空间 */}
        <Row gutter={16}>
          <Col span={24}>
            <Search
              placeholder="搜索日志内容、来源或级别..."
              value={filter.searchText}
              onChange={(e) => updateFilter({ searchText: e.target.value })}
              onSearch={(value) => updateFilter({ searchText: value })}
              allowClear
              size="small"
            />
          </Col>
        </Row>
        <Row gutter={16} style={{ marginTop: 8 }}>
          <Col span={8}>
            <Select
              mode="multiple"
              placeholder="选择日志类型"
              value={filter.logTypes}
              onChange={(value) => updateFilter({ logTypes: value })}
              size="small"
              style={{ width: '100%' }}
              tagRender={(props) => {
                const value = props.value as string
                if (!value) return null
                const typeInfo = getTypeInfo(value)
                return (
                  <Tag
                    color={typeInfo.color}
                    closable={props.closable}
                    onClose={props.onClose}
                    size="small"
                    style={{ margin: '2px' }}
                  >
                    {typeInfo.text}
                  </Tag>
                )
              }}
              optionLabelProp="label"
            >
              {availableOptions.types.map(type => {
                const typeInfo = getTypeInfo(type)
                return (
                  <Option key={type} value={type} label={typeInfo.text}>
                    <Tag color={typeInfo.color} size="small">
                      {typeInfo.text}
                    </Tag>
                  </Option>
                )
              })}
            </Select>
          </Col>
          <Col span={8}>
            <Select
              mode="multiple"
              placeholder="选择日志级别"
              value={filter.levels}
              onChange={(value) => updateFilter({ levels: value })}
              size="small"
              style={{ width: '100%' }}
              tagRender={(props) => {
                const value = props.value as string
                if (!value) return null
                return (
                  <Tag
                    color={getLevelColor(value)}
                    closable={props.closable}
                    onClose={props.onClose}
                    size="small"
                    style={{ margin: '2px' }}
                  >
                    {value}
                  </Tag>
                )
              }}
              optionLabelProp="label"
            >
              <Option value="DEBUG" label="DEBUG">
                <Tag color="default" size="small">DEBUG</Tag>
              </Option>
              <Option value="INFO" label="INFO">
                <Tag color="blue" size="small">INFO</Tag>
              </Option>
              <Option value="WARNING" label="WARNING">
                <Tag color="orange" size="small">WARNING</Tag>
              </Option>
              <Option value="ERROR" label="ERROR">
                <Tag color="red" size="small">ERROR</Tag>
              </Option>
              <Option value="CRITICAL" label="CRITICAL">
                <Tag color="magenta" size="small">CRITICAL</Tag>
              </Option>
            </Select>
          </Col>
          <Col span={8}>
            <Select
              mode="multiple"
              placeholder="选择日志源"
              value={filter.sources}
              onChange={(value) => updateFilter({ sources: value })}
              size="small"
              style={{ width: '100%' }}
            >
              {availableOptions.sources.map(source => (
                <Option key={source} value={source}>
                  {source}
                </Option>
              ))}
            </Select>
          </Col>
        </Row>

        {/* 高级选项 */}
        {showAdvanced && (
          <Collapse 
            size="small"
            items={[
              {
                key: 'advanced',
                label: '高级选项',
                children: (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    <Row gutter={16}>
                      <Col span={8}>
                        <Space>
                          <span>区分大小写:</span>
                          <Switch
                            checked={filter.caseSensitive}
                            onChange={(checked) => updateFilter({ caseSensitive: checked })}
                            size="small"
                          />
                        </Space>
                      </Col>
                      <Col span={8}>
                        <Space>
                          <span>正则表达式:</span>
                          <Switch
                            checked={filter.useRegex}
                            onChange={(checked) => updateFilter({ useRegex: checked })}
                            size="small"
                          />
                        </Space>
                      </Col>
                      <Col span={8}>
                        <Space>
                          <span>最大行数:</span>
                          <Slider
                            min={100}
                            max={5000}
                            step={100}
                            value={filter.maxLines}
                            onChange={(value) => updateFilter({ maxLines: value })}
                            style={{ width: 100 }}
                          />
                          <span>{filter.maxLines}</span>
                        </Space>
                      </Col>
                    </Row>

                    <Row gutter={16}>
                      <Col span={12}>
                        <span>时间范围:</span>
                        <RangePicker
                          showTime
                          value={filter.timeRange}
                          onChange={(dates) => updateFilter({ timeRange: dates as [Dayjs, Dayjs] })}
                          size="small"
                          style={{ width: '100%', marginLeft: 8 }}
                        />
                      </Col>
                      <Col span={12}>
                        <Space>
                          <span>显示选项:</span>
                          <Tooltip title="显示时间戳">
                            <Button
                              type={filter.showTimestamp ? 'primary' : 'default'}
                              icon={filter.showTimestamp ? <EyeOutlined /> : <EyeInvisibleOutlined />}
                              onClick={() => updateFilter({ showTimestamp: !filter.showTimestamp })}
                              size="small"
                            >
                              时间
                            </Button>
                          </Tooltip>
                          <Tooltip title="显示级别">
                            <Button
                              type={filter.showLevel ? 'primary' : 'default'}
                              icon={filter.showLevel ? <EyeOutlined /> : <EyeInvisibleOutlined />}
                              onClick={() => updateFilter({ showLevel: !filter.showLevel })}
                              size="small"
                            >
                              级别
                            </Button>
                          </Tooltip>
                          <Tooltip title="显示来源">
                            <Button
                              type={filter.showSource ? 'primary' : 'default'}
                              icon={filter.showSource ? <EyeOutlined /> : <EyeInvisibleOutlined />}
                              onClick={() => updateFilter({ showSource: !filter.showSource })}
                              size="small"
                            >
                              来源
                            </Button>
                          </Tooltip>
                        </Space>
                      </Col>
                    </Row>
                  </Space>
                )
              }
            ]}
          />
        )}
      </Space>
    </Card>
  )
}

export default LogSearchFilter
