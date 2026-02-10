import React, { useState, useEffect } from 'react'
import { Drawer, Button, Space } from 'antd'
import { CloseOutlined, SaveOutlined } from '@ant-design/icons'
import RuleProjectForm from './RuleProjectForm'
import CodeProjectForm from './CodeProjectForm'
import FileProjectForm from './FileProjectForm'
import { projectService } from '@/services/projects'
import type { Project, ProjectCreateRequest } from '@/types'
import styles from './ProjectEditDrawer.module.css'

interface ProjectEditDrawerProps {
  open: boolean
  onClose: () => void
  project: Project | null
  onSuccess?: () => void
}

const ProjectEditDrawer: React.FC<ProjectEditDrawerProps> = ({
  open,
  onClose,
  project,
  onSuccess
}) => {
  const [loading, setLoading] = useState(false)
  
  // 表单引用和验证状态
  const [ruleFormRef, setRuleFormRef] = useState<{ submit: () => void } | null>(null)
  const [ruleFormValid, setRuleFormValid] = useState(true)
  const [ruleFormTooltip, setRuleFormTooltip] = useState('')
  
  const [codeFormRef, setCodeFormRef] = useState<{ submit: () => void } | null>(null)
  const [codeFormValid, setCodeFormValid] = useState(true)
  const [codeFormTooltip, setCodeFormTooltip] = useState('')
  
  const [fileFormRef, setFileFormRef] = useState<{ submit: () => void } | null>(null)
  const [fileFormValid, setFileFormValid] = useState(true)
  const [fileFormTooltip, setFileFormTooltip] = useState('')

  // 重置状态
  const resetState = () => {
    setLoading(false)
    setRuleFormRef(null)
    setCodeFormRef(null)
    setFileFormRef(null)
  }

  useEffect(() => {
    if (!open) {
      resetState()
    }
  }, [open])

  // 处理规则项目表单验证
  const handleRuleValidationChange = (isValid: boolean, tooltip: string) => {
    setRuleFormValid(isValid)
    setRuleFormTooltip(tooltip)
  }

  // 处理代码项目表单验证
  const handleCodeValidationChange = (isValid: boolean, tooltip: string) => {
    setCodeFormValid(isValid)
    setCodeFormTooltip(tooltip)
  }

  // 处理文件项目表单验证
  const handleFileValidationChange = (isValid: boolean, tooltip: string) => {
    setFileFormValid(isValid)
    setFileFormTooltip(tooltip)
  }

  // 处理项目更新提交
  const handleSubmit = async (data: ProjectCreateRequest) => {
    if (!project) return
    
    try {
      setLoading(true)
      
      // 统一使用通用的项目更新接口，后端会根据type字段处理不同类型的项目
      await projectService.updateProject(project.id, data)
      
      // 成功提示由拦截器统一处理
      onSuccess?.()
      onClose()
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      setLoading(false)
    }
  }

  // 准备初始数据
  const getInitialData = () => {
    if (!project) return {}

    const baseData = {
      name: project.name,
      description: project.description,
      tags: Array.isArray(project.tags) ? project.tags.join(', ') : project.tags
    }

    if (project.type === 'rule' && project.rule_info) {
      return {
        ...baseData,
        engine: project.rule_info.engine,
        target_url: project.rule_info.target_url,
        url_pattern: project.rule_info.url_pattern,
        request_delay: project.rule_info.request_delay / 1000,
        request_method: project.rule_info.request_method,
        callback_type: project.rule_info.callback_type,
        max_pages: project.rule_info.max_pages,
        start_page: project.rule_info.start_page,
        priority: project.rule_info.priority,
        retry_count: project.rule_info.retry_count,
        timeout: project.rule_info.timeout,
        dont_filter: project.rule_info.dont_filter,
        // 处理API数据格式
        extraction_rules: (() => {
          if (project.rule_info.extraction_rules) {
            if (Array.isArray(project.rule_info.extraction_rules)) {
              return JSON.stringify(project.rule_info.extraction_rules)
            }
            return project.rule_info.extraction_rules
          }
          return '[]'
        })(),
        pagination_config: (() => {
          if (project.rule_info.pagination_config) {
            if (typeof project.rule_info.pagination_config === 'object') {
              return JSON.stringify(project.rule_info.pagination_config)
            }
            return project.rule_info.pagination_config
          }
          return JSON.stringify({ method: 'none', max_pages: 10, start_page: 1 })
        })(),
        headers: (() => {
          const headers = project.rule_info.headers
          if (!headers) return ''
          if (typeof headers === 'string') return headers
          return JSON.stringify(headers, null, 2)
        })(),
        cookies: (() => {
          const cookies = project.rule_info.cookies
          if (!cookies) return ''
          if (typeof cookies === 'string') return cookies
          return JSON.stringify(cookies, null, 2)
        })(),
        proxy_config: project.rule_info.proxy_config,
        anti_spider: project.rule_info.anti_spider,
        task_config: project.rule_info.task_config,
        data_schema: project.rule_info.data_schema
      }
    }

    if (project.type === 'code' && project.code_info) {
      return {
        ...baseData,
        language: project.code_info.language,
        version: project.code_info.version,
        code_entry_point: project.code_info.entry_point,
        documentation: project.code_info.documentation,
        code_content: project.code_info.content,
        dependencies: project.dependencies || []
      }
    }

    if (project.type === 'file' && project.file_info) {
      return {
        ...baseData,
        entry_point: project.file_info.entry_point,
        runtime_config: project.file_info.runtime_config ? JSON.stringify(project.file_info.runtime_config, null, 2) : '',
        environment_vars: project.file_info.environment_vars ? JSON.stringify(project.file_info.environment_vars, null, 2) : '',
        dependencies: project.dependencies || [],
        file_info: project.file_info
      }
    }

    return baseData
  }

  // 渲染当前步骤内容
  const renderStepContent = () => {
    if (!project) return null

    const initialData = getInitialData()
    const commonProps = {
      initialData,
      onSubmit: handleSubmit,
      loading,
      isEdit: true
    }

    // 直接显示项目配置表单
    switch (project.type) {
      case 'rule':
        return <RuleProjectForm {...commonProps} onValidationChange={handleRuleValidationChange} onRef={setRuleFormRef} />
      case 'code':
        return <CodeProjectForm {...commonProps} onValidationChange={handleCodeValidationChange} onRef={setCodeFormRef} />
      case 'file':
        return <FileProjectForm {...commonProps} onValidationChange={handleFileValidationChange} onRef={setFileFormRef} />
      default:
        return null
    }
  }

  // 渲染底部按钮
  const renderFooter = () => {
    // 获取提交按钮状态
    const getSubmitButtonProps = () => {
      switch (project?.type) {
        case 'rule':
          return { disabled: !ruleFormValid, tooltip: ruleFormTooltip }
        case 'code':
          return { disabled: !codeFormValid, tooltip: codeFormTooltip }
        case 'file':
          return { disabled: !fileFormValid, tooltip: fileFormTooltip }
        default:
          return { disabled: false, tooltip: '' }
      }
    }

    const submitProps = getSubmitButtonProps()

    const handleSave = () => {
      // 提交表单
      if (project?.type === 'rule' && ruleFormRef) {
        ruleFormRef.submit()
      } else if (project?.type === 'code' && codeFormRef) {
        codeFormRef.submit()
      } else if (project?.type === 'file' && fileFormRef) {
        fileFormRef.submit()
      }
    }

    return (
      <div className={styles.footer}>
        <Button onClick={onClose}>
          取消
        </Button>
        
        <Button
          type="primary"
          loading={loading}
          disabled={submitProps.disabled}
          onClick={handleSave}
          icon={<SaveOutlined />}
          title={submitProps.tooltip}
        >
          保存修改
        </Button>
      </div>
    )
  }

  if (!project) return null

  return (
    <Drawer
      title={
        <Space>
          <span>编辑项目 - {project.name}</span>
        </Space>
      }
      width={800}
      open={open}
      onClose={onClose}
      closeIcon={<CloseOutlined />}
      footer={renderFooter()}
      destroyOnHidden
      className={styles.drawer}
    >
      <div className={styles.content}>
        {renderStepContent()}
      </div>
    </Drawer>
  )
}

export default ProjectEditDrawer
