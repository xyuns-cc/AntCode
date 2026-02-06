import React, { useState, useEffect } from 'react'
import {
  Card,
  Tabs,
  Form,
  Input,
  InputNumber,
  Switch,
  Button,
  Space,
  Alert,
  Skeleton,
  Typography,
  message,
} from 'antd'
import {
  SettingOutlined,
  SaveOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  LineChartOutlined,
  FileTextOutlined,
} from '@ant-design/icons'
import { systemConfigService } from '@/services/systemConfig'
import type {
  TaskResourceConfig,
  TaskLogConfig,
  SchedulerConfig,
  CacheConfig,
  MonitoringConfig,
} from '@/types/system-config'
import { CONFIG_FIELD_LABELS, CONFIG_FIELD_DESCRIPTIONS } from '@/types/system-config'
import showNotification from '@/utils/notification'
import styles from './SystemConfig.module.css'

const { Title, Paragraph } = Typography

type TaskLogFormValues = TaskLogConfig & { task_log_max_size_mb?: number }

const SystemConfig: React.FC = () => {
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [reloading, setReloading] = useState(false)
  const [taskResourceForm] = Form.useForm<TaskResourceConfig>()
  const [taskLogForm] = Form.useForm<TaskLogFormValues>()
  const [schedulerForm] = Form.useForm<SchedulerConfig>()
  const [cacheForm] = Form.useForm<CacheConfig>()
  const [monitoringForm] = Form.useForm<MonitoringConfig>()

  // 加载配置
  const loadConfigs = React.useCallback(async () => {
    setLoading(true)
    try {
      const response = await systemConfigService.getConfigsByCategory()
      if (response.code === 200 && response.data) {
        // 设置表单初始值
        taskResourceForm.setFieldsValue(response.data.task_resource)
        // 将字节转换为 MB 显示
        taskLogForm.setFieldsValue({
          ...response.data.task_log,
          task_log_max_size_mb: Math.round(response.data.task_log.task_log_max_size / (1024 * 1024)),
        })
        schedulerForm.setFieldsValue(response.data.scheduler)
        cacheForm.setFieldsValue(response.data.cache)
        monitoringForm.setFieldsValue(response.data.monitoring)
      }
    } catch (error) {
      const messageText = error instanceof Error ? error.message : '加载系统配置失败'
      showNotification('error', '加载失败', messageText)
    } finally {
      setLoading(false)
    }
  }, [cacheForm, monitoringForm, schedulerForm, taskLogForm, taskResourceForm])

  useEffect(() => {
    loadConfigs()
  }, [loadConfigs])

  // 保存配置
  const handleSave = async (category: string, values: object) => {
    setSaving(true)
    try {
      // 构建批量更新请求
      const configUpdates = Object.entries(values as Record<string, unknown>).map(([key, value]) => ({
        config_key: key,
        config_value: String(value),
        category,
        is_active: true,
      }))

      const response = await systemConfigService.batchUpdateConfigs({
        configs: configUpdates,
      })

      if (response.code === 200) {
        showNotification('success', '保存成功', `已更新 ${response.data.updated_count} 个配置项`)
        await loadConfigs()
      }
    } catch (error) {
      const messageText = error instanceof Error ? error.message : '保存配置失败'
      showNotification('error', '保存失败', messageText)
    } finally {
      setSaving(false)
    }
  }

  // 热加载配置
  const handleReload = async () => {
    setReloading(true)
    try {
      const response = await systemConfigService.reloadConfigs()
      if (response.code === 200) {
        message.success({
          content: (
            <div>
              <div>✅ 配置已重新加载，大部分配置立即生效</div>
              <div style={{ fontSize: '12px', marginTop: '4px', opacity: 0.8 }}>
                ⚠️ 如修改了【并发任务数】或【时区】，请重启服务
              </div>
            </div>
          ),
          duration: 5,
        })
      }
    } catch (error) {
      const messageText = error instanceof Error ? error.message : '重新加载配置失败'
      showNotification('error', '重新加载失败', messageText)
    } finally {
      setReloading(false)
    }
  }

  // 任务资源配置表单
  const TaskResourceForm = () => (
    <Card
      title={
        <Space>
          <ThunderboltOutlined />
          任务资源配置
        </Space>
      }
      extra={
        <Button
          type="primary"
          icon={<SaveOutlined />}
          loading={saving}
          onClick={() => taskResourceForm.submit()}
        >
          保存配置
        </Button>
      }
    >
      <Form
        form={taskResourceForm}
        layout="vertical"
        onFinish={(values) => handleSave('task_resource', values)}
      >
        <Form.Item
          label={
            <span>
              {CONFIG_FIELD_LABELS.max_concurrent_tasks}
              <span style={{ color: '#ff4d4f', marginLeft: '4px', fontSize: '12px' }}>
                *需重启
              </span>
            </span>
          }
          name="max_concurrent_tasks"
          rules={[{ required: true, message: '请输入最大并发任务数' }]}
          tooltip={`${CONFIG_FIELD_DESCRIPTIONS.max_concurrent_tasks}。⚠️ 此配置修改后需要重启服务才能生效。`}
        >
          <InputNumber min={1} max={100} style={{ width: '100%' }} />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.task_execution_timeout}
          name="task_execution_timeout"
          rules={[{ required: true, message: '请输入任务执行超时时间' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.task_execution_timeout}
        >
          <InputNumber min={60} max={86400} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.task_cpu_time_limit}
          name="task_cpu_time_limit"
          rules={[{ required: true, message: '请输入任务CPU时间限制' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.task_cpu_time_limit}
        >
          <InputNumber min={60} max={3600} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.task_memory_limit}
          name="task_memory_limit"
          rules={[{ required: true, message: '请输入任务内存限制' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.task_memory_limit}
        >
          <InputNumber min={128} max={8192} style={{ width: '100%' }} addonAfter="MB" />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.task_max_retries}
          name="task_max_retries"
          rules={[{ required: true, message: '请输入任务最大重试次数' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.task_max_retries}
        >
          <InputNumber min={0} max={10} style={{ width: '100%' }} />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.task_retry_delay}
          name="task_retry_delay"
          rules={[{ required: true, message: '请输入任务重试延迟' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.task_retry_delay}
        >
          <InputNumber min={10} max={600} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>
      </Form>
    </Card>
  )

  // 任务日志配置表单
  const TaskLogForm = () => {
    // 保存时将 MB 转换为字节
    const handleTaskLogSave = (values: { task_log_max_size_mb?: number; task_log_retention_days?: number }) => {
      const convertedValues: Record<string, unknown> = {
        ...values,
        task_log_max_size: (values.task_log_max_size_mb || 0) * 1024 * 1024,
      }
      delete convertedValues.task_log_max_size_mb
      handleSave('task_log', convertedValues)
    }

    return (
      <Card
        title={
          <Space>
            <FileTextOutlined />
            任务日志配置
          </Space>
        }
        extra={
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={saving}
            onClick={() => taskLogForm.submit()}
          >
            保存配置
          </Button>
        }
      >
        <Form
          form={taskLogForm}
          layout="vertical"
          onFinish={handleTaskLogSave}
        >
          <Form.Item
            label={CONFIG_FIELD_LABELS.task_log_retention_days}
            name="task_log_retention_days"
            rules={[{ required: true, message: '请输入日志保留天数' }]}
            tooltip={CONFIG_FIELD_DESCRIPTIONS.task_log_retention_days}
          >
            <InputNumber min={1} max={365} style={{ width: '100%' }} addonAfter="天" />
          </Form.Item>

          <Form.Item
            label={CONFIG_FIELD_LABELS.task_log_max_size}
            name="task_log_max_size_mb"
            rules={[{ required: true, message: '请输入日志最大大小' }]}
            tooltip={CONFIG_FIELD_DESCRIPTIONS.task_log_max_size}
          >
            <InputNumber min={1} max={1024} style={{ width: '100%' }} addonAfter="MB" />
          </Form.Item>
        </Form>
      </Card>
    )
  }

  // 调度器配置表单
  const SchedulerForm = () => (
    <Card
      title={
        <Space>
          <ClockCircleOutlined />
          调度器配置
        </Space>
      }
      extra={
        <Button
          type="primary"
          icon={<SaveOutlined />}
          loading={saving}
          onClick={() => schedulerForm.submit()}
        >
          保存配置
        </Button>
      }
    >
      <Form
        form={schedulerForm}
        layout="vertical"
        onFinish={(values) => handleSave('scheduler', values)}
      >
        <Form.Item
          label={
            <span>
              {CONFIG_FIELD_LABELS.scheduler_timezone}
              <span style={{ color: '#ff4d4f', marginLeft: '4px', fontSize: '12px' }}>
                *需重启
              </span>
            </span>
          }
          name="scheduler_timezone"
          rules={[{ required: true, message: '请输入调度器时区' }]}
          tooltip={`${CONFIG_FIELD_DESCRIPTIONS.scheduler_timezone}。⚠️ 此配置修改后需要重启服务才能生效。`}
        >
          <Input placeholder="Asia/Shanghai" />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.cleanup_workspace_on_completion}
          name="cleanup_workspace_on_completion"
          valuePropName="checked"
          tooltip={CONFIG_FIELD_DESCRIPTIONS.cleanup_workspace_on_completion}
        >
          <Switch />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.cleanup_workspace_max_age_hours}
          name="cleanup_workspace_max_age_hours"
          rules={[{ required: true, message: '请输入工作空间最大保留时间' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.cleanup_workspace_max_age_hours}
        >
          <InputNumber min={1} max={168} style={{ width: '100%' }} addonAfter="小时" />
        </Form.Item>
      </Form>
    </Card>
  )

  // 缓存配置表单
  const CacheForm = () => (
    <Card
      title={
        <Space>
          <DatabaseOutlined />
          缓存配置
        </Space>
      }
      extra={
        <Button
          type="primary"
          icon={<SaveOutlined />}
          loading={saving}
          onClick={() => cacheForm.submit()}
        >
          保存配置
        </Button>
      }
    >
      <Form
        form={cacheForm}
        layout="vertical"
        onFinish={(values) => handleSave('cache', values)}
      >
        <Form.Item
          label={CONFIG_FIELD_LABELS.cache_enabled}
          name="cache_enabled"
          valuePropName="checked"
          tooltip={CONFIG_FIELD_DESCRIPTIONS.cache_enabled}
        >
          <Switch />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.cache_default_ttl}
          name="cache_default_ttl"
          rules={[{ required: true, message: '请输入默认缓存TTL' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.cache_default_ttl}
        >
          <InputNumber min={60} max={3600} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.metrics_cache_ttl}
          name="metrics_cache_ttl"
          rules={[{ required: true, message: '请输入指标缓存TTL' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.metrics_cache_ttl}
        >
          <InputNumber min={10} max={300} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.api_cache_ttl}
          name="api_cache_ttl"
          rules={[{ required: true, message: '请输入API缓存TTL' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.api_cache_ttl}
        >
          <InputNumber min={60} max={3600} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.users_cache_ttl}
          name="users_cache_ttl"
          rules={[{ required: true, message: '请输入用户缓存TTL' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.users_cache_ttl}
        >
          <InputNumber min={60} max={3600} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.query_cache_ttl}
          name="query_cache_ttl"
          rules={[{ required: true, message: '请输入查询缓存TTL' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.query_cache_ttl}
        >
          <InputNumber min={60} max={3600} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>

        <Form.Item
          label={
            <span>
              {CONFIG_FIELD_LABELS.metrics_background_update}
              <span style={{ color: '#ff4d4f', marginLeft: '4px', fontSize: '12px' }}>
                *需重启
              </span>
            </span>
          }
          name="metrics_background_update"
          valuePropName="checked"
          tooltip={`${CONFIG_FIELD_DESCRIPTIONS.metrics_background_update}。⚠️ 此配置修改后需要重启服务才能生效。`}
        >
          <Switch />
        </Form.Item>

        <Form.Item
          label={
            <span>
              {CONFIG_FIELD_LABELS.metrics_update_interval}
              <span style={{ color: '#ff4d4f', marginLeft: '4px', fontSize: '12px' }}>
                *需重启
              </span>
            </span>
          }
          name="metrics_update_interval"
          rules={[{ required: true, message: '请输入指标更新间隔' }]}
          tooltip={`${CONFIG_FIELD_DESCRIPTIONS.metrics_update_interval}。⚠️ 此配置修改后需要重启服务才能生效。`}
        >
          <InputNumber min={5} max={300} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>
      </Form>
    </Card>
  )

  // 监控配置表单
  const MonitoringForm = () => (
    <Card
      title={
        <Space>
          <LineChartOutlined />
          监控配置
        </Space>
      }
      extra={
        <Button
          type="primary"
          icon={<SaveOutlined />}
          loading={saving}
          onClick={() => monitoringForm.submit()}
        >
          保存配置
        </Button>
      }
    >
      <Form
        form={monitoringForm}
        layout="vertical"
        onFinish={(values) => handleSave('monitoring', values)}
      >
        <Form.Item
          label={CONFIG_FIELD_LABELS.monitoring_enabled}
          name="monitoring_enabled"
          valuePropName="checked"
          tooltip={CONFIG_FIELD_DESCRIPTIONS.monitoring_enabled}
        >
          <Switch />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.monitor_status_ttl}
          name="monitor_status_ttl"
          rules={[{ required: true, message: '请输入监控状态TTL' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.monitor_status_ttl}
        >
          <InputNumber min={60} max={3600} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.monitor_history_ttl}
          name="monitor_history_ttl"
          rules={[{ required: true, message: '请输入监控历史TTL' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.monitor_history_ttl}
        >
          <InputNumber min={600} max={86400} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.monitor_history_keep_days}
          name="monitor_history_keep_days"
          rules={[{ required: true, message: '请输入监控历史保留天数' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.monitor_history_keep_days}
        >
          <InputNumber min={1} max={365} style={{ width: '100%' }} addonAfter="天" />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.monitor_cluster_ttl}
          name="monitor_cluster_ttl"
          rules={[{ required: true, message: '请输入集群状态TTL' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.monitor_cluster_ttl}
        >
          <InputNumber min={60} max={3600} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.monitor_stream_batch_size}
          name="monitor_stream_batch_size"
          rules={[{ required: true, message: '请输入监控流批处理大小' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.monitor_stream_batch_size}
        >
          <InputNumber min={10} max={1000} style={{ width: '100%' }} />
        </Form.Item>

        <Form.Item
          label={
            <span>
              {CONFIG_FIELD_LABELS.monitor_stream_interval}
              <span style={{ color: '#ff4d4f', marginLeft: '4px', fontSize: '12px' }}>
                *需重启
              </span>
            </span>
          }
          name="monitor_stream_interval"
          rules={[{ required: true, message: '请输入监控流处理间隔' }]}
          tooltip={`${CONFIG_FIELD_DESCRIPTIONS.monitor_stream_interval}。⚠️ 此配置修改后需要重启服务才能生效。`}
        >
          <InputNumber min={30} max={600} style={{ width: '100%' }} addonAfter="秒" />
        </Form.Item>

        <Form.Item
          label={CONFIG_FIELD_LABELS.monitor_stream_maxlen}
          name="monitor_stream_maxlen"
          rules={[{ required: true, message: '请输入监控流最大长度' }]}
          tooltip={CONFIG_FIELD_DESCRIPTIONS.monitor_stream_maxlen}
        >
          <InputNumber min={1000} max={100000} style={{ width: '100%' }} />
        </Form.Item>
      </Form>
    </Card>
  )

  if (loading) {
    return (
      <div className={styles.systemConfigContainer}>
        <div className={styles.pageHeader}>
          <div>
            <Skeleton.Input active style={{ width: 200, height: 32, marginBottom: 8 }} />
            <Skeleton.Input active style={{ width: 300, height: 20 }} />
          </div>
          <Skeleton.Button active style={{ width: 120 }} />
        </div>
        <Skeleton active paragraph={{ rows: 12 }} />
      </div>
    )
  }

  return (
    <div className={styles.systemConfigContainer}>
      <div className={styles.pageHeader}>
        <div>
          <Title level={2} className={styles.pageTitle}>
            <SettingOutlined style={{ marginRight: 8 }} />
            系统配置管理
          </Title>
          <Paragraph className={styles.pageDescription}>
            管理系统全局配置，修改后可实时热加载生效
          </Paragraph>
        </div>
        <Button
          type="default"
          icon={<ReloadOutlined />}
          loading={reloading}
          onClick={handleReload}
        >
          热加载配置
        </Button>
      </div>

      <Alert
        message="配置说明"
        description={
          <div>
            <p>• 此页面只有超级管理员（admin用户）可以访问</p>
            <p>• 修改配置后请点击【热加载配置】按钮使配置立即生效</p>
            <p>• ⚠️ 注意：以下配置修改后需要重启服务才能完全生效：</p>
            <p style={{ paddingLeft: 16 }}>【最大并发任务数】【调度器时区】【启用指标后台更新】【指标更新间隔】【监控流处理间隔】</p>
            <p>• 其他配置项均支持热加载，无需重启服务</p>
          </div>
        }
        type="warning"
        showIcon
        closable
        className={styles.alertInfo}
      />

      <Tabs
        defaultActiveKey="task_resource"
        size="large"
        className={styles.tabsContainer}
        destroyInactiveTabPane={false}
        items={[
          {
            key: 'task_resource',
            label: (
              <span>
                <ThunderboltOutlined />
                任务资源
              </span>
            ),
            children: <TaskResourceForm />,
          },
          {
            key: 'task_log',
            label: (
              <span>
                <FileTextOutlined />
                任务日志
              </span>
            ),
            children: <TaskLogForm />,
          },
          {
            key: 'scheduler',
            label: (
              <span>
                <ClockCircleOutlined />
                调度器
              </span>
            ),
            children: <SchedulerForm />,
          },
          {
            key: 'cache',
            label: (
              <span>
                <DatabaseOutlined />
                缓存
              </span>
            ),
            children: <CacheForm />,
          },
          {
            key: 'monitoring',
            label: (
              <span>
                <LineChartOutlined />
                监控
              </span>
            ),
            children: <MonitoringForm />,
          },
        ]}
      />
    </div>
  )
}

export default SystemConfig
