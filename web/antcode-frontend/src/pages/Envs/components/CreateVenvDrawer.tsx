import React, { useState } from 'react'
import {
  Button,
  Drawer,
  Select,
  Input,
  Tag,
  Card,
  Steps,
  Upload,
  Modal,
  Empty,
  theme,
} from 'antd'
import {
  PlusOutlined,
  SettingOutlined,
  InboxOutlined,
  CodeOutlined,
  EditOutlined,
  DownloadOutlined,
  ArrowLeftOutlined,
  ArrowRightOutlined,
} from '@ant-design/icons'
import envService from '@/services/envs'
import type { CreateVenvDrawerProps } from '../types'
import styles from '@/components/envs/EnvDrawer.module.css'

const { Dragger } = Upload

const CreateVenvDrawer: React.FC<CreateVenvDrawerProps> = ({ onCreated }) => {
  const { token } = theme.useToken()
  const [open, setOpen] = useState(false)
  const [installedInterpreters, setInstalledInterpreters] = useState<
    Array<{ version: string; source?: string; python_bin: string }>
  >([])
  const [version, setVersion] = useState<string>('')
  const [interpreterSource, setInterpreterSource] = useState<string>('mise')
  const [pythonBin, setPythonBin] = useState<string>('')
  const [sharedKey, setSharedKey] = useState<string>('')
  const [deps, setDeps] = useState<string[]>([])
  const [uploading, setUploading] = useState(false)
  const [currentStep, setCurrentStep] = useState(0)

  const openDrawer = async () => {
    setOpen(true)
    setCurrentStep(0)
    try {
      const list = await envService.listInterpreters()
      setInstalledInterpreters(
        list as Array<{ version: string; source?: string; python_bin: string }>
      )
    } catch {
      setInstalledInterpreters([])
    }
  }

  const onUpload = async (file: File) => {
    setUploading(true)
    try {
      const text = await file.text()
      const lines = text
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter((l) => l && !l.startsWith('#'))
      setDeps(Array.from(new Set([...(deps || []), ...lines])))
    } finally {
      setUploading(false)
    }
    return false
  }

  const submit = async () => {
    if (!version) return
    const info = (await envService.createSharedVenv({
      version,
      shared_venv_key: sharedKey || undefined,
      interpreter_source: interpreterSource,
      python_bin: interpreterSource === 'local' ? pythonBin : undefined,
    })) as { venv_id?: number }
    if (deps && deps.length && info && info.venv_id) {
      await envService.installPackagesToVenv(String(info.venv_id), deps)
    }
    setOpen(false)
    setVersion('')
    setInterpreterSource('mise')
    setPythonBin('')
    setDeps([])
    setSharedKey('')
    onCreated()
  }

  const steps = [
    { title: '基础配置', description: '选择Python版本', icon: <SettingOutlined /> },
    { title: '依赖管理', description: '添加项目依赖', icon: <InboxOutlined /> },
  ]

  const renderStepContent = () => {
    switch (currentStep) {
      case 0:
        return (
          <div className={styles.formContent}>
            <div className={styles.formItem}>
              <div className={styles.formLabel}>
                <CodeOutlined />
                <span>Python 版本</span>
              </div>
              <Select
                showSearch
                placeholder="选择已安装的解释器"
                value={version}
                onChange={(val, option) => {
                  setVersion(val as string)
                  const opt = option as { source?: string; python_bin?: string }
                  setInterpreterSource(opt?.source || 'mise')
                  setPythonBin(opt?.python_bin || '')
                }}
                options={(installedInterpreters || []).map((it) => ({
                  value: it.version,
                  label: `${it.version} (${it.source || 'mise'})`,
                  source: it.source || 'mise',
                  python_bin: it.python_bin,
                }))}
                allowClear
                style={{ width: '100%' }}
                size="large"
                notFoundContent={
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="暂无可用解释器，请先添加"
                  />
                }
              />
              <div className={styles.formHint}>列表来自"解释器"管理，支持本地与 mise</div>
            </div>

            <div className={styles.formItem}>
              <div className={styles.formLabel}>
                <EditOutlined />
                <span>共享标识（可选）</span>
              </div>
              <Input
                placeholder="输入共享标识，便于项目引用"
                value={sharedKey}
                onChange={(e) => setSharedKey(e.target.value)}
                size="large"
              />
              <div className={styles.formHint}>
                设置标识后，其他项目可以通过此标识复用该虚拟环境
              </div>
            </div>
          </div>
        )
      case 1:
        return (
          <div className={styles.formContent}>
            <div className={styles.formItem}>
              <div className={styles.formLabel}>
                <DownloadOutlined />
                <span>项目依赖</span>
              </div>
              <Select
                mode="tags"
                placeholder="输入依赖包名后回车，如: requests==2.32.3"
                value={deps}
                onChange={(value: string[]) => setDeps(value)}
                tokenSeparators={[',', ' ']}
                style={{ width: '100%' }}
                size="large"
              />
              <div className={styles.formHint}>支持多个依赖包，可以指定版本号</div>
            </div>

            <div className={styles.formItem}>
              <div className={styles.formLabel}>
                <InboxOutlined />
                <span>上传依赖文件</span>
              </div>
              <Dragger
                accept=".txt"
                showUploadList={false}
                beforeUpload={(file) => {
                  onUpload(file)
                  return false
                }}
                className={styles.uploadArea}
                style={{ padding: '24px 0' }}
              >
                <p className="ant-upload-drag-icon">
                  <InboxOutlined style={{ fontSize: 40, color: token.colorPrimary }} />
                </p>
                <p className="ant-upload-text">点击或拖拽 requirements.txt 到此区域</p>
                <p className="ant-upload-hint">支持自动解析依赖文件</p>
              </Dragger>
            </div>

            {deps.length > 0 && (
              <div className={styles.depsPreview}>
                <div className={styles.depsCount}>
                  已添加 <strong>{deps.length}</strong> 个依赖包
                </div>
                <div className={styles.tagList}>
                  {deps.slice(0, 8).map((dep) => (
                    <Tag key={dep} closable onClose={() => setDeps(deps.filter((d) => d !== dep))}>
                      {dep}
                    </Tag>
                  ))}
                  {deps.length > 8 && <Tag color="blue">+{deps.length - 8} 更多</Tag>}
                </div>
                <Button
                  type="link"
                  size="small"
                  danger
                  onClick={() => setDeps([])}
                  style={{ padding: '4px 0', marginTop: 8 }}
                >
                  清空所有
                </Button>
              </div>
            )}
          </div>
        )
      default:
        return null
    }
  }

  const handleNext = () => {
    if (currentStep === 0 && !version) {
      Modal.warning({ title: '请选择Python版本' })
      return
    }
    setCurrentStep((prev) => prev + 1)
  }

  const handlePrev = () => {
    setCurrentStep((prev) => prev - 1)
  }

  const renderFooter = () => (
    <div className={styles.footerButtons}>
      <Button onClick={() => setOpen(false)} disabled={uploading}>
        取消
      </Button>
      {currentStep > 0 && (
        <Button icon={<ArrowLeftOutlined />} onClick={handlePrev} disabled={uploading}>
          上一步
        </Button>
      )}
      {currentStep === 0 && (
        <Button
          type="primary"
          icon={<ArrowRightOutlined />}
          onClick={handleNext}
          disabled={!version}
        >
          下一步
        </Button>
      )}
      {currentStep === 1 && (
        <Button type="primary" icon={<PlusOutlined />} loading={uploading} onClick={submit}>
          创建虚拟环境
        </Button>
      )}
    </div>
  )

  return (
    <>
      <Button type="primary" icon={<PlusOutlined />} onClick={openDrawer}>
        创建虚拟环境
      </Button>
      <Drawer
        title="创建虚拟环境"
        placement="right"
        width={720}
        open={open}
        onClose={() => setOpen(false)}
        maskClosable={false}
        destroyOnHidden
        footer={renderFooter()}
        styles={{ footer: { textAlign: 'right' } }}
        className={styles.drawer}
      >
        <div className={styles.steps}>
          <Steps current={currentStep} items={steps} size="small" />
        </div>
        <Card variant="borderless" className={styles.formCard}>
          {renderStepContent()}
        </Card>
      </Drawer>
    </>
  )
}

export default CreateVenvDrawer
