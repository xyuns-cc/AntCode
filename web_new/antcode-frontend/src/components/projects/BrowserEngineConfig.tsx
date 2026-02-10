import React from 'react'
import { Card, Form, Switch, Input, InputNumber, Row, Col, Typography, Tooltip, Space, Divider } from 'antd'
import { 
  SettingOutlined, 
  EyeInvisibleOutlined, 
  PictureOutlined, 
  SoundOutlined,
  SafetyOutlined,
  QuestionCircleOutlined,
  CodeOutlined
} from '@ant-design/icons'

const { Text } = Typography

export interface BrowserEngineSettings {
  headless?: boolean
  no_imgs?: boolean
  mute?: boolean
  incognito?: boolean
  window_size?: string
  page_load_timeout?: number
  extra_arguments?: string
  user_agent?: string
}

interface BrowserEngineConfigProps {
  value?: BrowserEngineSettings
  onChange?: (value: BrowserEngineSettings) => void
  disabled?: boolean
}

const BrowserEngineConfig: React.FC<BrowserEngineConfigProps> = ({
  value = {},
  onChange,
  disabled = false
}) => {
  const handleChange = (field: keyof BrowserEngineSettings, fieldValue: unknown) => {
    onChange?.({
      ...value,
      [field]: fieldValue
    })
  }

  return (
    <Card 
      title={
        <Space>
          <SettingOutlined />
          浏览器引擎配置
        </Space>
      }
      size="small"
    >
      <Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
        配置 DrissionPage 浏览器引擎的运行参数，这些设置会影响采集性能和反检测能力
      </Text>

      {/* 基础开关配置 */}
      <Row gutter={[16, 16]}>
        <Col span={6}>
          <Form.Item
            label={
              <Space>
                <EyeInvisibleOutlined />
                无头模式
                <Tooltip title="无头模式下浏览器不显示界面，资源消耗更低">
                  <QuestionCircleOutlined style={{ color: '#999' }} />
                </Tooltip>
              </Space>
            }
            style={{ marginBottom: 0 }}
          >
            <Switch
              checked={value.headless ?? true}
              onChange={(checked) => handleChange('headless', checked)}
              disabled={disabled}
              checkedChildren="开"
              unCheckedChildren="关"
            />
          </Form.Item>
        </Col>

        <Col span={6}>
          <Form.Item
            label={
              <Space>
                <PictureOutlined />
                禁用图片
                <Tooltip title="禁用图片加载可显著提升采集速度">
                  <QuestionCircleOutlined style={{ color: '#999' }} />
                </Tooltip>
              </Space>
            }
            style={{ marginBottom: 0 }}
          >
            <Switch
              checked={value.no_imgs ?? false}
              onChange={(checked) => handleChange('no_imgs', checked)}
              disabled={disabled}
              checkedChildren="开"
              unCheckedChildren="关"
            />
          </Form.Item>
        </Col>

        <Col span={6}>
          <Form.Item
            label={
              <Space>
                <SoundOutlined />
                静音模式
                <Tooltip title="禁用浏览器音频输出">
                  <QuestionCircleOutlined style={{ color: '#999' }} />
                </Tooltip>
              </Space>
            }
            style={{ marginBottom: 0 }}
          >
            <Switch
              checked={value.mute ?? true}
              onChange={(checked) => handleChange('mute', checked)}
              disabled={disabled}
              checkedChildren="开"
              unCheckedChildren="关"
            />
          </Form.Item>
        </Col>

        <Col span={6}>
          <Form.Item
            label={
              <Space>
                <SafetyOutlined />
                匿名模式
                <Tooltip title="使用隐私/匿名模式，不保存浏览记录和 Cookie">
                  <QuestionCircleOutlined style={{ color: '#999' }} />
                </Tooltip>
              </Space>
            }
            style={{ marginBottom: 0 }}
          >
            <Switch
              checked={value.incognito ?? false}
              onChange={(checked) => handleChange('incognito', checked)}
              disabled={disabled}
              checkedChildren="开"
              unCheckedChildren="关"
            />
          </Form.Item>
        </Col>
      </Row>

      <Divider style={{ margin: '16px 0' }} />

      {/* 高级配置 */}
      <Row gutter={16}>
        <Col span={8}>
          <Form.Item
            label="窗口大小"
            tooltip="浏览器窗口尺寸，格式: 宽,高"
          >
            <Input
              value={value.window_size ?? '1920,1080'}
              onChange={(e) => handleChange('window_size', e.target.value)}
              disabled={disabled}
              placeholder="1920,1080"
            />
          </Form.Item>
        </Col>

        <Col span={8}>
          <Form.Item
            label="页面加载超时(秒)"
            tooltip="页面加载的最大等待时间"
          >
            <InputNumber
              value={value.page_load_timeout ?? 30}
              onChange={(val) => handleChange('page_load_timeout', val)}
              disabled={disabled}
              min={5}
              max={300}
              style={{ width: '100%' }}
            />
          </Form.Item>
        </Col>

        <Col span={8}>
          <Form.Item
            label="User-Agent"
            tooltip="自定义浏览器 User-Agent，留空使用默认值"
          >
            <Input
              value={value.user_agent}
              onChange={(e) => handleChange('user_agent', e.target.value)}
              disabled={disabled}
              placeholder="留空使用默认 UA"
            />
          </Form.Item>
        </Col>
      </Row>

      <Form.Item
        label={
          <Space>
            <CodeOutlined />
            自定义启动参数
            <Tooltip title="Chrome 命令行参数，多个参数用逗号分隔，如: --disable-web-security,--allow-running-insecure-content">
              <QuestionCircleOutlined style={{ color: '#999' }} />
            </Tooltip>
          </Space>
        }
      >
        <Input.TextArea
          value={value.extra_arguments}
          onChange={(e) => handleChange('extra_arguments', e.target.value)}
          disabled={disabled}
          placeholder="--disable-web-security,--allow-running-insecure-content"
          rows={2}
        />
      </Form.Item>
    </Card>
  )
}

export default BrowserEngineConfig
