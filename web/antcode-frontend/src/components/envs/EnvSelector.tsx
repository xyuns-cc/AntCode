/**
 * 环境选择器组件
 * 用于项目创建时选择运行环境（本地或 Worker）
 */
import type React from 'react'
import { useState, useEffect } from 'react'
import { Card, Radio, Select, Input, Space, Typography, Alert } from 'antd'
import { CloudServerOutlined, DesktopOutlined } from '@ant-design/icons'
import type { Worker } from '@/types'
import envService from '@/services/envs'

const { Title, Text } = Typography
const { Option } = Select

/**
 * 环境配置类型
 */
export interface EnvironmentConfig {
  // 环境位置：本地 or Worker
  location: 'local' | 'worker'
  
  // Worker ID（location=worker时必填）
  workerId?: string
  
  // 环境作用域
  scope: 'private' | 'shared'
  
  // 是否使用现有环境
  useExisting: boolean
  
  // 使用现有环境时：环境名称
  existingEnvName?: string
  
  // 创建新环境时：Python版本
  pythonVersion?: string
  
  // 创建新环境时：环境名称（可选）
  envName?: string
  
  // 创建新环境时：环境描述（可选）
  envDescription?: string
}

interface EnvSelectorProps {
  value?: EnvironmentConfig | null
  onChange?: (config: EnvironmentConfig) => void
  workerList?: Worker[]
}

/**
 * 环境选择器组件
 */
const EnvSelector: React.FC<EnvSelectorProps> = ({
  value,
  onChange,
  workerList = []
}) => {
  // 环境位置
  const [location, setLocation] = useState<'local' | 'worker'>(value?.location || 'local')
  
  // Worker ID
  const [workerId, setWorkerId] = useState<string | undefined>(value?.workerId)
  
  // 环境作用域
  const [scope, setScope] = useState<'private' | 'shared'>(value?.scope || 'private')
  
  // 是否使用现有环境
  const [useExisting, setUseExisting] = useState<boolean>(value?.useExisting || false)
  
  // 现有环境列表
  const [existingEnvs, setExistingEnvs] = useState<Array<{ name: string; python_version: string }>>([])
  
  // 选中的现有环境
  const [existingEnvName, setExistingEnvName] = useState<string | undefined>(value?.existingEnvName)
  
  // 解释器列表（包含版本和来源）
  interface InterpreterItem {
    version: string
    source?: string
  }
  const [interpreters, setInterpreters] = useState<InterpreterItem[]>([])
  
  // 选中的Python版本
  const [pythonVersion, setPythonVersion] = useState<string | undefined>(value?.pythonVersion)
  
  // 环境名称
  const [envName, setEnvName] = useState<string | undefined>(value?.envName)
  
  // 环境描述
  const [envDescription, setEnvDescription] = useState<string | undefined>(value?.envDescription)

  // 加载已安装的Python解释器列表
  useEffect(() => {
    if (!useExisting) {
      if (location === 'local') {
        // 加载本地已安装的解释器
        envService.listInstalledInterpreters()
          .then(data => {
            setInterpreters(data.map(i => ({ version: i.version, source: i.source })))
          })
          .catch(() => setInterpreters([]))
      } else if (location === 'worker' && workerId) {
        // 加载 Worker 已安装的解释器
        envService.listWorkerInterpreters(workerId)
          .then(data => {
            setInterpreters(data.interpreters.map(i => ({ version: i.version, source: i.source })))
          })
          .catch(() => setInterpreters([]))
      }
    }
  }, [location, useExisting, workerId])

  // 加载现有环境列表
  useEffect(() => {
    if (useExisting) {
      if (location === 'worker' && workerId) {
        // 加载 Worker 环境
        envService.listWorkerEnvs(workerId)
          .then(envs => setExistingEnvs(envs))
          .catch(() => setExistingEnvs([]))
      } else if (location === 'local') {
        // 加载本地环境
        envService.listVenvs({ page: 1, size: 100 })
          .then(res => setExistingEnvs(
            res.items.map(item => ({
              name: item.key || item.venv_path,
              python_version: item.version
            }))
          ))
          .catch(() => setExistingEnvs([]))
      }
    }
  }, [useExisting, location, workerId])

  // 触发onChange
  useEffect(() => {
    const config: EnvironmentConfig = {
      location,
      workerId,
      scope,
      useExisting,
      existingEnvName,
      pythonVersion,
      envName,
      envDescription
    }
    onChange?.(config)
  }, [location, workerId, scope, useExisting, existingEnvName, pythonVersion, envName, envDescription, onChange])

  // 在线 Worker 列表
  const onlineWorkers = workerList.filter(w => w.status === 'online')

  return (
    <Card title="运行环境配置" style={{ marginBottom: 16 }}>
      <Space direction="vertical" style={{ width: '100%' }} size="large">
        {/* 环境位置选择 */}
        <div>
          <Title level={5}>环境位置</Title>
          <Radio.Group
            value={location}
            onChange={e => {
              setLocation(e.target.value)
              setWorkerId(undefined)
            }}
          >
            <Radio.Button value="local">
              <DesktopOutlined /> 本地
            </Radio.Button>
            <Radio.Button value="worker" disabled={onlineWorkers.length === 0}>
              <CloudServerOutlined /> Worker
            </Radio.Button>
          </Radio.Group>
          {location === 'worker' && onlineWorkers.length === 0 && (
            <Alert
              message="暂无在线 Worker"
              type="warning"
              showIcon
              style={{ marginTop: 8 }}
            />
          )}
        </div>

        {/* Worker 选择 */}
        {location === 'worker' && (
          <div>
            <Title level={5}>选择 Worker</Title>
            <Select
              style={{ width: '100%' }}
              placeholder="选择要使用的 Worker"
              value={workerId}
              onChange={setWorkerId}
            >
              {onlineWorkers.map(worker => (
                <Option key={worker.id} value={worker.id}>
                  {worker.name}
                </Option>
              ))}
            </Select>
          </div>
        )}

        {/* 环境作用域 */}
        <div>
          <Title level={5}>环境作用域</Title>
          <Radio.Group value={scope} onChange={e => setScope(e.target.value)}>
            <Radio.Button value="private">私有</Radio.Button>
            <Radio.Button value="shared">共享</Radio.Button>
          </Radio.Group>
          <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {scope === 'private' 
                ? '私有环境仅当前项目使用' 
                : '共享环境可被多个项目使用'}
            </Text>
          </div>
        </div>

        {/* 使用现有环境 or 创建新环境 */}
        <div>
          <Title level={5}>环境选择</Title>
          <Radio.Group value={useExisting} onChange={e => setUseExisting(e.target.value)}>
            <Radio.Button value={false}>创建新环境</Radio.Button>
            <Radio.Button value={true}>使用现有环境</Radio.Button>
          </Radio.Group>
        </div>

        {/* 使用现有环境 */}
        {useExisting && (
          <div>
            <Title level={5}>选择环境</Title>
            <Select
              style={{ width: '100%' }}
              placeholder="选择要使用的环境"
              value={existingEnvName}
              onChange={setExistingEnvName}
            >
              {existingEnvs.map(env => (
                <Option key={env.name} value={env.name}>
                  {env.name} (Python {env.python_version})
                </Option>
              ))}
            </Select>
          </div>
        )}

        {/* 创建新环境 */}
        {!useExisting && (
          <>
            {/* Python版本选择 - 统一从已安装的解释器中选择 */}
            <div>
              <Title level={5}>Python版本</Title>
              <Select
                style={{ width: '100%' }}
                placeholder={interpreters.length > 0 ? "选择Python版本" : "加载中..."}
                value={pythonVersion}
                onChange={setPythonVersion}
                showSearch
                loading={interpreters.length === 0 && ((location === 'worker' && workerId !== undefined) || location === 'local')}
                notFoundContent={interpreters.length === 0 ? "暂无可用版本，请先在环境管理中添加解释器" : undefined}
              >
                {interpreters.map(item => {
                  // 来源标识
                  const sourceLabel = item.source === 'local' ? '本地' 
                    : item.source === 'mise' ? 'mise'
                    : item.source === 'system' ? '系统'
                    : item.source || ''
                  return (
                    <Option key={item.version} value={item.version}>
                      {item.version}
                      {sourceLabel && (
                        <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                          ({sourceLabel})
                        </Text>
                      )}
                    </Option>
                  )
                })}
              </Select>
              <div style={{ marginTop: 8 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {location === 'worker' 
                    ? '从 Worker 已安装的解释器中选择，如需添加请在 Worker 管理中操作'
                    : '从本地已安装的解释器中选择，如需添加请在环境管理中操作'}
                </Text>
              </div>
            </div>

            <div>
              <Title level={5}>环境名称（可选）</Title>
              <Input
                placeholder="例如: my-project-env"
                value={envName}
                onChange={e => setEnvName(e.target.value)}
              />
            </div>

            <div>
              <Title level={5}>环境描述（可选）</Title>
              <Input.TextArea
                placeholder="环境的用途说明"
                value={envDescription}
                onChange={e => setEnvDescription(e.target.value)}
                rows={2}
              />
            </div>
          </>
        )}
      </Space>
    </Card>
  )
}

export default EnvSelector
