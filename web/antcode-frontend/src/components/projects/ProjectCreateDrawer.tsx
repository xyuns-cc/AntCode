import React, { useEffect, useState, memo } from 'react'
import {
  Drawer,
  Steps,
  Button,
  Space,
  Card,
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
import RegionNodeSelector from './RegionNodeSelector'
import EnvSelector from '@/components/envs/EnvSelector'
import type { EnvironmentConfig } from '@/components/envs/EnvSelector'
import { projectService } from '@/services/projects'
import nodeService from '@/services/nodes'
import type { ProjectType, ProjectCreateRequest, Node, Project } from '@/types'
import Logger from '@/utils/logger'
import styles from './ProjectCreateDrawer.module.css'

interface ProjectCreateDrawerProps {
  open: boolean
  onClose: () => void
  onSuccess?: (project: Project) => void
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
  
  // 环境配置状态（使用新的 EnvSelector）
  const [envConfig, setEnvConfig] = useState<EnvironmentConfig | null>(null)
  const [nodeList, setNodeList] = useState<Node[]>([])
  
  // 规则项目的区域配置
  const [regionConfig, setRegionConfig] = useState<{ region?: string; require_render?: boolean }>({})

  useEffect(() => {
    if (open) {
      // 加载节点列表
      nodeService.getAllNodes().then(setNodeList).catch(() => setNodeList([]))
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
    setEnvConfig(null)
    setRegionConfig({})
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
    // 规则项目只需要区域配置，不需要环境配置
    if (projectType === 'rule') {
      setLoading(true)
      try {
        Logger.log('创建规则项目:', finalData, '区域配置:', regionConfig)
        
        // 规则项目合并区域配置
        const merged: ProjectCreateRequest = {
          ...finalData,
          region: regionConfig.region,
          // 规则项目使用默认的环境配置
          venv_scope: 'shared',
          python_version: '3.11',
        }

        const project = await projectService.createProject(merged)
        onSuccess?.(project)
        handleClose()
      } catch (error) {
        Logger.error('创建规则项目失败:', error)
      } finally {
        setLoading(false)
      }
      return
    }
    
    // 文件/代码项目需要环境配置
    if (!envConfig) {
      showNotification.error('请配置运行环境')
      return
    }

    // 验证节点环境配置
    if (envConfig.location === 'node' && !envConfig.nodeId) {
      showNotification.error('使用节点环境时必须选择节点')
      return
    }

    // 验证使用现有环境时的配置
    if (envConfig.useExisting && !envConfig.existingEnvName) {
      showNotification.error('请选择要使用的环境')
      return
    }

    // 验证创建新环境时的配置
    if (!envConfig.useExisting && !envConfig.pythonVersion) {
      showNotification.error('创建新环境时必须指定Python版本')
      return
    }

    setLoading(true)
    try {
      Logger.log('创建项目:', finalData, '环境配置:', envConfig)
      
      // 合并环境配置
      const merged: ProjectCreateRequest = {
        ...finalData,
        
        // 环境位置和节点信息
        env_location: envConfig.location,
        node_id: envConfig.nodeId,
        
        // 环境作用域
        venv_scope: envConfig.scope,
        
        // 使用现有环境 or 创建新环境
        use_existing_env: envConfig.useExisting,
        existing_env_name: envConfig.existingEnvName,
        
        // 创建新环境时的配置
        python_version: envConfig.pythonVersion,
        env_name: envConfig.envName,
        env_description: envConfig.envDescription,
      }

      const project = await projectService.createProject(merged)
      onSuccess?.(project)
      handleClose()
    } catch (error) {
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
      case 1: {
        if (!projectType) return null
        
        const commonProps = {
          initialData: formData,
          onDataChange: handleFormDataChange,
          onSubmit: handleSubmit,
          loading
        }

        // 使用新的环境选择器组件
        const envSection = (
          <EnvSelector 
            value={envConfig}
            onChange={setEnvConfig}
            nodeList={nodeList}
          />
        )

        switch (projectType) {
          case 'file':
            return <>
              {envSection}
              <FileProjectForm {...commonProps} onValidationChange={handleFileValidationChange} onRef={setFileFormRef} />
            </>
          case 'rule':
            // 规则项目使用区域选择器，不需要环境配置
            return <>
              <Card title="执行区域配置" size="small" style={{ marginBottom: 16 }}>
                <RegionNodeSelector
                  value={regionConfig}
                  onChange={(config) => {
                    setRegionConfig(config)
                    // 根据引擎类型更新 require_render
                    if (formData.engine === 'browser') {
                      setRegionConfig({ ...config, require_render: true })
                    }
                  }}
                  requireRender={formData.engine === 'browser'}
                />
              </Card>
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
