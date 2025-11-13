import React, { useEffect, useState, memo } from 'react'
import {
  Drawer,
  Steps,
  Button,
  Space,
  Card,
  Typography,
  Row,
  Col,
  Select,
  Input,
} from 'antd'
import showNotification from '@/utils/notification'
import {
  FileOutlined,
  SettingOutlined,
  CodeOutlined,
  ArrowLeftOutlined,
  ArrowRightOutlined
} from '@ant-design/icons'
import ProjectTypeSelector from './ProjectTypeSelector'
import FileProjectForm from './FileProjectForm'
import RuleProjectForm from './RuleProjectForm'
import CodeProjectForm from './CodeProjectForm'
import { projectService } from '@/services/projects'
import type { ProjectType, ProjectCreateRequest } from '@/types'
import Logger from '@/utils/logger'
import styles from './ProjectCreateDrawer.module.css'
import envService from '@/services/envs'

const { Title, Text } = Typography

interface ProjectCreateDrawerProps {
  open: boolean
  onClose: () => void
  onSuccess?: (project: any) => void
}

const ProjectCreateDrawer: React.FC<ProjectCreateDrawerProps> = memo(({
  open,
  onClose,
  onSuccess
}) => {
  const [currentStep, setCurrentStep] = useState(0)
  const [projectType, setProjectType] = useState<ProjectType | null>(null)
  const [loading, setLoading] = useState(false)
  const [formData, setFormData] = useState<Partial<ProjectCreateRequest>>({})
  const [ruleFormValid, setRuleFormValid] = useState(false)
  const [ruleFormTooltip, setRuleFormTooltip] = useState('')
  const [ruleFormRef, setRuleFormRef] = useState<{ submit: () => void } | null>(null)
  
  const [fileFormValid, setFileFormValid] = useState(false)
  const [fileFormTooltip, setFileFormTooltip] = useState('')
  const [fileFormRef, setFileFormRef] = useState<{ submit: () => void } | null>(null)
  
  const [codeFormValid, setCodeFormValid] = useState(false)
  const [codeFormTooltip, setCodeFormTooltip] = useState('')
  const [codeFormRef, setCodeFormRef] = useState<{ submit: () => void } | null>(null)
  
  // 环境选择状态（统一样式 + 与项目创建一致）
  const [venvScope, setVenvScope] = useState<'private' | 'shared'>('private')
  const [installedInterpreters, setInstalledInterpreters] = useState<Array<{ version: string; source?: string; python_bin: string }>>([])
  const [pythonVersion, setPythonVersion] = useState<string>('')
  const [interpreterSource, setInterpreterSource] = useState<'mise' | 'local'>('mise')
  const [pythonBin, setPythonBin] = useState<string>('')
  const [sharedVenvs, setSharedVenvs] = useState<{ key: string; version: string }[]>([])
  const [dependencies, setDependencies] = useState<string[]>([])
  const [sharedKey, setSharedKey] = useState<string>('')

  useEffect(() => {
    if (open) {
      // 加载已安装解释器与共享环境
      envService.listInterpreters().then((list) => setInstalledInterpreters(list as any)).catch(() => setInstalledInterpreters([]))
      envService.listVenvs({ scope: 'shared', page: 1, size: 100 }).then(res => {
        const items = (res.items || []).map(v => ({ key: v.key || v.version, version: v.version }))
        setSharedVenvs(items)
      }).catch(() => setSharedVenvs([]))
    }
  }, [open])

  // 步骤配置
  const steps = [
    {
      title: '选择类型',
      description: '选择项目类型',
      icon: <SettingOutlined />
    },
    {
      title: '配置项目',
      description: '填写项目信息',
      icon: projectType === 'file' ? <FileOutlined /> : 
            projectType === 'rule' ? <SettingOutlined /> : <CodeOutlined />
    }
  ]

  // 重置状态
  const resetState = () => {
    setCurrentStep(0)
    setProjectType(null)
    setFormData({})
    setLoading(false)
  }

  // 关闭抽屉
  const handleClose = () => {
    resetState()
    onClose()
  }

  // 下一步
  const handleNext = () => {
    if (currentStep === 0 && !projectType) {
      showNotification('warning', '请选择项目类型')
      return
    }
    setCurrentStep(prev => prev + 1)
  }

  // 上一步
  const handlePrev = () => {
    setCurrentStep(prev => prev - 1)
  }

  // 项目类型选择
  const handleTypeSelect = (type: ProjectType) => {
    setProjectType(type)
    setFormData({ type })
  }

  // 表单数据更新
  const handleFormDataChange = (data: Partial<ProjectCreateRequest>) => {
    setFormData(prev => ({ ...prev, ...data }))
  }

  // 处理规则表单验证状态变化
  const handleRuleValidationChange = (isValid: boolean, tooltip: string) => {
    setRuleFormValid(isValid)
    setRuleFormTooltip(tooltip)
  }

  // 处理文件表单验证状态变化
  const handleFileValidationChange = (isValid: boolean, tooltip: string) => {
    setFileFormValid(isValid)
    setFileFormTooltip(tooltip)
  }

  // 处理代码表单验证状态变化
  const handleCodeValidationChange = (isValid: boolean, tooltip: string) => {
    setCodeFormValid(isValid)
    setCodeFormTooltip(tooltip)
  }

  // 提交创建
  const handleSubmit = async (finalData: ProjectCreateRequest) => {
    setLoading(true)
    try {
      Logger.log('创建项目:', finalData)
      // 合并环境配置
      const merged: any = {
        ...finalData,
        venv_scope: venvScope,
        python_version: pythonVersion,
        shared_venv_key: venvScope === 'shared' ? undefined : undefined, // 共享环境仅在环境管理创建
        dependencies,
        interpreter_source: interpreterSource,
        python_bin: interpreterSource === 'local' ? pythonBin : undefined,
      }
      if (venvScope === 'shared') {
        merged.shared_venv_key = sharedKey || undefined
      }
      const project = await projectService.createProject(merged)
      onSuccess?.(project)
      handleClose()
    } catch (error: any) {
      Logger.error('创建项目失败:', error)
    } finally {
      setLoading(false)
    }
  }

  // 渲染步骤内容
  const renderStepContent = () => {
    switch (currentStep) {
      case 0:
        return (
          <ProjectTypeSelector
            selectedType={projectType}
            onSelect={handleTypeSelect}
          />
        )
      case 1:
        if (!projectType) return null
        
        const commonProps = {
          initialData: formData,
          onDataChange: handleFormDataChange,
          onSubmit: handleSubmit,
          loading
        }

        // 先渲染环境选择区域
        const envSection = (
          <div style={{ marginBottom: 16 }}>
            <Title level={5}>运行环境</Title>
            <div style={{ marginBottom: 12 }}>
              <Text>作用域</Text>
              <Select
                style={{ width: '100%', marginTop: 6 }}
                value={venvScope}
                onChange={(v) => setVenvScope(v)}
                options={[{ value: 'private', label: '私有（项目专属）' }, { value: 'shared', label: '公共（共享）' }]}
              />
            </div>
            {venvScope === 'private' ? (
              <>
                <div style={{ marginBottom: 12 }}>
                  <Text>Python 解释器（来源）</Text>
                  <Select
                    showSearch
                    placeholder="选择已安装的解释器（local/mise）"
                    style={{ width: '100%', marginTop: 6 }}
                    value={pythonVersion}
                    onChange={(val, option: any) => {
                      setPythonVersion(val as string)
                      setInterpreterSource((option?.source as 'mise' | 'local') || 'mise')
                      setPythonBin(option?.python_bin as string)
                    }}
                    options={(installedInterpreters || []).map((it: any) => ({ value: it.version, label: `${it.version} (${it.source || 'mise'})`, source: it.source || 'mise', python_bin: it.python_bin }))}
                    filterOption={(input, option) => ((option?.label as string) || '').toLowerCase().includes(input.toLowerCase())}
                  />
                </div>
                <div style={{ marginBottom: 12 }}>
                  <Text>依赖（至少一个）</Text>
                  <Select
                    mode="tags"
                    placeholder="输入依赖包名后回车，如: requests==2.32.3"
                    style={{ width: '100%', marginTop: 6 }}
                    value={dependencies}
                    onChange={(vals) => setDependencies(vals as string[])}
                    tokenSeparators={[',']}
                  />
                </div>
              </>
            ) : (
              <div style={{ marginBottom: 12 }}>
                <Text>共享环境</Text>
                <Select
                  placeholder="请选择已有共享环境"
                  style={{ width: '100%', marginTop: 6 }}
                  value={sharedKey}
                  onChange={(v) => setSharedKey(v)}
                  options={(sharedVenvs || []).map(v => ({ value: v.key, label: `${v.key} (${v.version})` }))}
                />
                <Text type="secondary" style={{ display: 'block', marginTop: 4 }}>共享环境需已在“环境管理”中创建</Text>
              </div>
            )}
          </div>
        )

        switch (projectType) {
          case 'file':
            return <>
              {envSection}
              <FileProjectForm {...commonProps} onValidationChange={handleFileValidationChange} onRef={setFileFormRef} />
            </>
          case 'rule':
            return <>
              {envSection}
              <RuleProjectForm {...commonProps} onValidationChange={handleRuleValidationChange} onRef={setRuleFormRef} />
            </>
          case 'code':
            return <>
              {envSection}
              <CodeProjectForm {...commonProps} onValidationChange={handleCodeValidationChange} onRef={setCodeFormRef} />
            </>
          default:
            return null
        }
      default:
        return null
    }
  }

  // 渲染底部按钮
  const renderFooter = () => {
    return (
      <Space>
        {currentStep > 0 && (
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={handlePrev}
            disabled={loading}
          >
            上一步
          </Button>
        )}
        
        {currentStep === 0 && (
          <Button
            type="primary"
            icon={<ArrowRightOutlined />}
            onClick={handleNext}
            disabled={!projectType}
          >
            下一步
          </Button>
        )}
        
        {currentStep === 1 && projectType === 'file' && (
          <Button
            type="primary"
            loading={loading}
            disabled={!fileFormValid}
            title={fileFormTooltip}
            onClick={() => {
              if (fileFormRef) {
                fileFormRef.submit()
              }
            }}
          >
            创建文件项目
          </Button>
        )}
        
        {currentStep === 1 && projectType === 'rule' && (
          <Button
            type="primary"
            loading={loading}
            disabled={!ruleFormValid}
            title={ruleFormTooltip}
            onClick={() => {
              if (ruleFormRef) {
                ruleFormRef.submit()
              }
            }}
          >
            创建规则项目
          </Button>
        )}
        
        {currentStep === 1 && projectType === 'code' && (
          <Button
            type="primary"
            loading={loading}
            disabled={!codeFormValid}
            title={codeFormTooltip}
            onClick={() => {
              if (codeFormRef) {
                codeFormRef.submit()
              }
            }}
          >
            创建代码项目
          </Button>
        )}
        
        <Button onClick={handleClose} disabled={loading}>
          取消
        </Button>
      </Space>
    )
  }

  return (
    <Drawer
      title="创建新项目"
      placement="right"
      width={720}
      open={open}
      onClose={handleClose}
      maskClosable={false}
      footer={renderFooter()}
      styles={{ footer: { textAlign: 'right' } }}
      className={styles.drawer}
    >
      <div className={styles.steps}>
        <Steps
          current={currentStep}
          items={steps}
          size="small"
        />
      </div>

      <Card variant="borderless" className={styles.formCard}>
        {renderStepContent()}
      </Card>
    </Drawer>
  )
})

export default ProjectCreateDrawer
