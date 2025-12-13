import React, { useState } from 'react'
import {
  Button,
  Drawer,
  Select,
  Input,
  Card,
  Steps,
  Empty,
  Typography,
  theme,
} from 'antd'
import {
  PlusOutlined,
  SettingOutlined,
  CodeOutlined,
  CloudServerOutlined,
  DesktopOutlined,
  RocketOutlined,
  ArrowLeftOutlined,
  ArrowRightOutlined,
} from '@ant-design/icons'
import envService from '@/services/envs'
import { interpreterSourceOptions } from '@/config/displayConfig'
import type { InterpreterDrawerProps } from '../types'
import styles from '@/components/envs/EnvDrawer.module.css'

const InterpreterDrawer: React.FC<InterpreterDrawerProps> = ({ onAdded, currentNode }) => {
  const { token } = theme.useToken()
  const [open, setOpen] = useState(false)
  const [currentStep, setCurrentStep] = useState(0)
  const [source, setSource] = useState<string>('mise')
  const [versions, setVersions] = useState<string[]>([])
  const [version, setVersion] = useState<string>('')
  const [pythonBin, setPythonBin] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [nodeHint, setNodeHint] = useState<string>('')

  const steps = [
    { title: '选择来源', description: 'mise 或 本地解释器', icon: <SettingOutlined /> },
    { title: '配置解释器', description: '选择版本或填写路径', icon: <CodeOutlined /> },
  ]

  const openDrawer = async () => {
    setOpen(true)
    setCurrentStep(0)
    setNodeHint(currentNode ? `当前节点: ${currentNode.name}` : '本地（主控）')
    try {
      if (currentNode) {
        const nodeVers = await envService.getNodePythonVersions(currentNode.id)
        const merged = Array.from(
          new Set([
            ...(nodeVers?.available || []),
            ...(nodeVers?.all_interpreters || [])
              .map((i: { version?: string }) => i.version)
              .filter(Boolean),
          ])
        ).sort()
        setVersions(merged)
      } else {
        const v = await envService.listPythonVersions()
        setVersions(v)
      }
    } catch {
      setVersions([])
    }
  }

  const submit = async () => {
    setLoading(true)
    try {
      if (source === 'mise') {
        if (!version) return
        if (currentNode) {
          await envService.installNodePythonVersion(currentNode.id, version)
        } else {
          await envService.installInterpreter(version)
        }
      } else {
        if (!pythonBin) return
        if (currentNode) {
          await envService.registerNodeInterpreter(currentNode.id, pythonBin)
        } else {
          await envService.registerLocalInterpreter(pythonBin)
        }
      }
      setOpen(false)
      setVersion('')
      setPythonBin('')
      onAdded()
    } finally {
      setLoading(false)
    }
  }

  const renderStepContent = () => {
    switch (currentStep) {
      case 0:
        return (
          <div className={styles.formContent}>
            {nodeHint && (
              <div className={styles.nodeHint}>
                <CloudServerOutlined />
                <span>{nodeHint}</span>
              </div>
            )}

            <div className={styles.formItem}>
              <div className={styles.formLabel}>
                <SettingOutlined />
                <span>解释器来源</span>
              </div>
              <Select
                value={source}
                onChange={(value: string) => setSource(value)}
                options={interpreterSourceOptions.map((opt) => ({
                  value: opt.value,
                  label: (
                    <span>
                      {opt.value === 'mise' ? (
                        <RocketOutlined style={{ marginRight: 8 }} />
                      ) : (
                        <DesktopOutlined style={{ marginRight: 8 }} />
                      )}
                      {opt.label}
                    </span>
                  ),
                }))}
                style={{ width: '100%' }}
                size="large"
              />
              <div className={styles.formHint}>
                {source === 'mise'
                  ? 'mise 是一个多语言版本管理工具，支持自动下载和管理多个 Python 版本'
                  : '使用系统已安装的 Python 解释器，需要提供完整路径'}
              </div>
            </div>
          </div>
        )
      case 1:
        return (
          <div className={styles.formContent}>
            <div className={styles.formItem}>
              <div className={styles.formLabel}>
                <CodeOutlined />
                <span>{source === 'mise' ? 'Python 版本' : 'Python 路径'}</span>
              </div>
              {source === 'mise' ? (
                <Select
                  showSearch
                  placeholder="选择要安装的版本"
                  value={version}
                  onChange={(value: string) => setVersion(value)}
                  options={(versions || []).map((v) => ({ value: v, label: v }))}
                  allowClear
                  style={{ width: '100%' }}
                  size="large"
                  notFoundContent={
                    <Empty
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description="未找到可用版本，请确认 mise 已安装"
                    />
                  }
                />
              ) : (
                <Input
                  placeholder="/usr/local/bin/python3"
                  value={pythonBin}
                  onChange={(e) => setPythonBin(e.target.value)}
                  style={{ width: '100%' }}
                  size="large"
                  prefix={<CodeOutlined style={{ color: token.colorTextTertiary }} />}
                />
              )}
              <div className={styles.formHint}>
                {source === 'mise' ? (
                  '选择要通过 mise 安装的 Python 版本'
                ) : (
                  <>
                    输入 Python 解释器的完整路径，可使用{' '}
                    <Typography.Text code>which python3</Typography.Text> 查找
                  </>
                )}
              </div>
            </div>
          </div>
        )
      default:
        return null
    }
  }

  const renderFooter = () => (
    <div className={styles.footerButtons}>
      <Button onClick={() => setOpen(false)} disabled={loading}>
        取消
      </Button>
      {currentStep > 0 && (
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => setCurrentStep((s) => s - 1)}
          disabled={loading}
        >
          上一步
        </Button>
      )}
      {currentStep === 0 && (
        <Button type="primary" icon={<ArrowRightOutlined />} onClick={() => setCurrentStep(1)}>
          下一步
        </Button>
      )}
      {currentStep === 1 && (
        <Button
          type="primary"
          icon={<PlusOutlined />}
          loading={loading}
          onClick={submit}
          disabled={(source === 'mise' && !version) || (source === 'local' && !pythonBin)}
        >
          添加解释器
        </Button>
      )}
    </div>
  )

  return (
    <>
      <Button type="primary" onClick={openDrawer}>
        添加解释器
      </Button>
      <Drawer
        title="添加解释器"
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

export default InterpreterDrawer
