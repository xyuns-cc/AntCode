import type { FC } from 'react'
import { Button, Col, DatePicker, Row, Slider, Space, Switch, Tooltip } from 'antd'
import { EyeInvisibleOutlined, EyeOutlined } from '@ant-design/icons'
import type { Dayjs } from 'dayjs'
import type { LogFilter, UpdateLogFilter } from './logSearchTypes'

const { RangePicker } = DatePicker

interface AdvancedControlsProps {
  filter: LogFilter
  updateFilter: UpdateLogFilter
}

interface VisibilityButtonProps {
  enabled: boolean
  label: string
  onChange: () => void
}

const VisibilityButton: FC<VisibilityButtonProps> = ({ enabled, label, onChange }) => <Tooltip title={`显示${label}`}>
  <Button
    type={enabled ? 'primary' : 'default'}
    icon={enabled ? <EyeOutlined /> : <EyeInvisibleOutlined />}
    onClick={onChange}
  >{label}</Button>
</Tooltip>

const SearchOptions: FC<AdvancedControlsProps> = ({ filter, updateFilter }) => <Row gutter={16}>
  <Col span={8}>
    <Space><span>区分大小写:</span>
      <Switch checked={filter.caseSensitive} onChange={(value) => updateFilter({ caseSensitive: value })} />
    </Space>
  </Col>
  <Col span={8}>
    <Space><span>正则表达式:</span>
      <Switch checked={filter.useRegex} onChange={(value) => updateFilter({ useRegex: value })} />
    </Space>
  </Col>
  <Col span={8}>
    <Space><span>最大行数:</span>
      <Slider
        min={100} max={5000} step={100} value={filter.maxLines}
        onChange={(value) => updateFilter({ maxLines: value })} style={{ width: 100 }}
      />
      <span>{filter.maxLines}</span>
    </Space>
  </Col>
</Row>

const toTimeRange = (dates: [Dayjs | null, Dayjs | null] | null): [Dayjs, Dayjs] | undefined => {
  if (!dates?.[0] || !dates[1]) return undefined
  return [dates[0], dates[1]]
}

const RangeAndVisibility: FC<AdvancedControlsProps> = ({ filter, updateFilter }) => <Row gutter={16}>
  <Col span={12}>
    <span>时间范围:</span>
    <RangePicker
      showTime value={filter.timeRange}
      onChange={(dates) => updateFilter({ timeRange: toTimeRange(dates) })}
      style={{ width: '100%', marginLeft: 8 }}
    />
  </Col>
  <Col span={12}>
    <Space><span>显示选项:</span>
      <VisibilityButton
        enabled={filter.showTimestamp !== false} label="时间"
        onChange={() => updateFilter({ showTimestamp: !filter.showTimestamp })}
      />
      <VisibilityButton
        enabled={filter.showLevel !== false} label="级别"
        onChange={() => updateFilter({ showLevel: !filter.showLevel })}
      />
      <VisibilityButton
        enabled={filter.showSource !== false} label="来源"
        onChange={() => updateFilter({ showSource: !filter.showSource })}
      />
    </Space>
  </Col>
</Row>

const LogFilterAdvancedControls: FC<AdvancedControlsProps> = (props) => <Space direction="vertical" style={{ width: '100%' }}>
  <SearchOptions {...props} />
  <RangeAndVisibility {...props} />
</Space>

export default LogFilterAdvancedControls
