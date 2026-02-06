/**
 * 环境选择器组件
 * 用于项目创建时选择运行环境
 */
import type React from 'react'
import { useState, useEffect, useMemo } from 'react'
import { Card, Select, Input, Space, Typography, Alert, Radio } from 'antd'
import { CloudServerOutlined } from '@ant-design/icons'
import type { Worker } from '@/types'
import { runtimeService, type RuntimeEnv } from '@/services/runtimes'

const { Title, Text } = Typography
const { Option } = Select

/**
 * 环境配置类型
 */
export interface EnvironmentConfig {
  // 环境位置
  location: 'worker'

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
  const [location] = useState<'worker'>('worker')

  // Worker ID
  const [workerId, setWorkerId] = useState<string | undefined>(value?.workerId)

  // 环境作用域
  const [scope, setScope] = useState<'private' | 'shared'>(value?.scope || 'private')

  // 是否使用现有环境
  const [useExisting, setUseExisting] = useState<boolean>(value?.useExisting || false)

  // 选中的现有环境
  const [existingEnvName, setExistingEnvName] = useState<string | undefined>(value?.existingEnvName)

  // 选中的Python版本
  const [pythonVersion, setPythonVersion] = useState<string | undefined>(value?.pythonVersion)

  // 环境名称
  const [envName, setEnvName] = useState<string | undefined>(value?.envName)

  // 环境描述
  const [envDescription, setEnvDescription] = useState<string | undefined>(value?.envDescription)

  const [envOptions, setEnvOptions] = useState<RuntimeEnv[]>([])
  const [envLoading, setEnvLoading] = useState(false)

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

  // 在线Worker 列表
  const onlineWorkers = workerList.filter((worker) => worker.status === 'online')

  const filteredEnvs = useMemo(
    () => envOptions.filter((env) => env.scope === scope),
    [envOptions, scope]
  )

  useEffect(() => {
    if (scope === 'shared' && !useExisting) {
      setUseExisting(true)
    }
    if (scope === 'shared') {
      setPythonVersion(undefined)
    }
  }, [scope, useExisting])

  useEffect(() => {
    if (!workerId) {
      setEnvOptions([])
      return
    }

    setEnvLoading(true)
    runtimeService
      .listEnvs(workerId, scope)
      .then((data) => {
        setEnvOptions(data)
      })
      .catch(() => setEnvOptions([]))
      .finally(() => setEnvLoading(false))
  }, [workerId, scope])

  useEffect(() => {
    if (existingEnvName && !filteredEnvs.find((env) => env.name === existingEnvName)) {
      setExistingEnvName(undefined)
    }
  }, [existingEnvName, filteredEnvs])

  return (
    <Card title="运行环境配置" style={{ marginBottom: 16 }}>
      <Space direction="vertical" style={{ width: '100%' }} size="large">
        {/* Worker选择 */}
        <div>
          <Title level={5}>
            <CloudServerOutlined style={{ marginRight: 8 }} />
            选择 Worker
          </Title>
          {onlineWorkers.length === 0 ? (
            <Alert
              message="暂无在线 Worker"
              description="请确保至少有一个 Worker在线，环境操作需要在 Worker上执行"
              type="warning"
              showIcon
            />
          ) : (
            <Select
              style={{ width: '100%' }}
              placeholder="选择要使用的 Worker"
              value={workerId}
              onChange={setWorkerId}
            >
              {onlineWorkers.map((worker) => (
                <Option key={worker.id} value={worker.id}>
                  {worker.name} {worker.transportMode !== 'direct' && worker.host ? `(${worker.host}:${worker.port})` : '(Direct)'}
                </Option>
              ))}
            </Select>
          )}
          <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              所有环境操作都在 Worker上执行
            </Text>
          </div>
        </div>

        {/* 环境作用域 */}
        <div>
          <Title level={5}>环境作用域</Title>
          <Radio.Group value={scope} onChange={e => setScope(e.target.value)}>
            <Radio.Button value="private">私有</Radio.Button>
            <Radio.Button value="shared">公共</Radio.Button>
          </Radio.Group>
          <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {scope === 'private'
                ? '私有环境仅当前项目使用'
                : '公共环境可被多个项目共享'}
            </Text>
          </div>
        </div>

        {/* 使用现有环境 or 创建新环境 */}
        <div>
          <Title level={5}>环境选择</Title>
          <Radio.Group
            value={useExisting}
            onChange={e => setUseExisting(e.target.value)}
            disabled={scope === 'shared'}
          >
            <Radio.Button value={false}>创建新环境</Radio.Button>
            <Radio.Button value={true}>使用现有环境</Radio.Button>
          </Radio.Group>
        </div>

        {/* 使用现有环境 */}
        {useExisting && (
          <div>
            <Title level={5}>选择环境</Title>
            {filteredEnvs.length === 0 ? (
              <Alert
                message="暂无可用环境"
                description={scope === 'shared' ? '请先在运行时管理中创建共享环境' : '当前 Worker 未发现可用环境'}
                type="warning"
                showIcon
              />
            ) : (
              <Select
                style={{ width: '100%' }}
                placeholder="选择已有环境"
                value={existingEnvName}
                onChange={setExistingEnvName}
                loading={envLoading}
              >
                {filteredEnvs.map((env) => (
                  <Option key={env.name} value={env.name}>
                    {env.name} (Python {env.python_version})
                  </Option>
                ))}
              </Select>
            )}
          </div>
        )}

        {/* 创建新环境 */}
        {!useExisting && (
          <>
            {/* Python版本选择 */}
            <div>
              <Title level={5}>Python版本</Title>
              <Input
                placeholder="例如 3.11.9"
                value={pythonVersion}
                onChange={(e) => setPythonVersion(e.target.value)}
              />
              <div style={{ marginTop: 8 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  建议与 Worker 运行环境保持一致
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
