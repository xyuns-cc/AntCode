import type { FC } from 'react'
import { Col, Input, Row, Select, Tag, Tooltip, Typography } from 'antd'
import { getLevelColor, getTypeInfo } from './logFilterUtils'
import type { LogFilter, LogFilterOptions, UpdateLogFilter } from './logSearchTypes'

const { Search } = Input
const STANDARD_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']

interface BasicControlsProps {
  filter: LogFilter
  options: LogFilterOptions
  regexError?: string
  updateFilter: UpdateLogFilter
}

const TypeSelect: FC<BasicControlsProps> = ({ filter, options, updateFilter }) => <Select
  mode="multiple"
  placeholder="选择日志类型"
  value={filter.logTypes}
  onChange={(value) => updateFilter({ logTypes: value })}
  style={{ width: '100%' }}
  tagRender={(props) => {
    const value = String(props.value ?? '')
    if (!value) return <span />
    const info = getTypeInfo(value)
    return <Tag color={info.color} closable={props.closable} onClose={props.onClose} style={{ margin: '2px' }}>
      {info.text}
    </Tag>
  }}
  optionLabelProp="label"
>
  {options.types.map((type) => {
    const info = getTypeInfo(type)
    return <Select.Option key={type} value={type} label={info.text}>
      <Tag color={info.color}>{info.text}</Tag>
    </Select.Option>
  })}
</Select>

const LevelSelect: FC<BasicControlsProps> = ({ filter, options, updateFilter }) => {
  const levels = [...new Set([...STANDARD_LEVELS, ...options.levels])]
  return <Select
  mode="multiple"
  placeholder="选择日志级别"
  value={filter.levels}
  onChange={(value) => updateFilter({ levels: value })}
  style={{ width: '100%' }}
  tagRender={(props) => {
    const value = String(props.value ?? '')
    if (!value) return <span />
    return <Tag color={getLevelColor(value)} closable={props.closable} onClose={props.onClose} style={{ margin: '2px' }}>
      {value}
    </Tag>
  }}
  optionLabelProp="label"
>
  {levels.map((level) => <Select.Option key={level} value={level} label={level}>
    <Tag color={getLevelColor(level)}>{level}</Tag>
  </Select.Option>)}
</Select>
}

const SourceSelect: FC<BasicControlsProps> = ({ filter, options, updateFilter }) => <Select
  mode="multiple"
  placeholder="选择日志源"
  value={filter.sources}
  onChange={(value) => updateFilter({ sources: value })}
  style={{ width: '100%' }}
>
  {options.sources.map((source) => <Select.Option key={source} value={source}>{source}</Select.Option>)}
</Select>

const LogFilterBasicControls: FC<BasicControlsProps> = (props) => <>
  <Row gutter={16}>
    <Col span={24}>
      <Tooltip title={props.regexError} open={props.regexError ? undefined : false}>
        <Search
          status={props.regexError ? 'error' : undefined}
          placeholder="搜索日志内容、来源或级别..."
          value={props.filter.searchText}
          onChange={(event) => props.updateFilter({ searchText: event.target.value })}
          onSearch={(value) => props.updateFilter({ searchText: value })}
          allowClear
        />
      </Tooltip>
      {props.regexError && <Typography.Text type="danger" role="alert">
        正则表达式无效: {props.regexError}
      </Typography.Text>}
    </Col>
  </Row>
  <Row gutter={16} style={{ marginTop: 8 }}>
    <Col span={8}><TypeSelect {...props} /></Col>
    <Col span={8}><LevelSelect {...props} /></Col>
    <Col span={8}><SourceSelect {...props} /></Col>
  </Row>
</>

export default LogFilterBasicControls
