import type React from 'react'
import { useState, useEffect } from 'react'
import { Drawer, Button, Space, Modal } from 'antd'
import { CloseOutlined, SaveOutlined } from '@ant-design/icons'
import showNotification from '@/utils/notification'
import RuleProjectForm from './RuleProjectForm'
import CodeProjectForm from './CodeProjectForm'
import FileProjectForm from './FileProjectForm'
import { projectService } from '@/services/projects'
import type {
  Project,
  ProjectCodeConfigUpdateRequest,
  ProjectFileConfigUpdateRequest,
  ProjectUpdateRequest,
} from '@/types'
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
  // 初始一律 false，与创建抽屉一致；子表单挂载并通过 onValidationChange 上报后才允许提交，
  // 防止初始 true 绕过必填校验导致保存空值。
  const [ruleFormRef, setRuleFormRef] = useState<{ submit: () => void } | null>(null)
  const [ruleFormValid, setRuleFormValid] = useState(false)
  const [ruleFormTooltip, setRuleFormTooltip] = useState('')

  const [codeFormRef, setCodeFormRef] = useState<{ submit: () => void } | null>(null)
  const [codeFormValid, setCodeFormValid] = useState(false)
  const [codeFormTooltip, setCodeFormTooltip] = useState('')

  const [fileFormRef, setFileFormRef] = useState<{ submit: () => void } | null>(null)
  const [fileFormValid, setFileFormValid] = useState(false)
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
  // 分步串行：基本信息 -> 类型配置。任一步失败时弹窗询问是否继续后续步骤，
  // 避免某一步失败后静默吞掉错误，用户不知道保存了哪一部分。
  const handleSubmit = async (data: Record<string, unknown>) => {
    if (!project) return

    setLoading(true)
    try {
      const baseUpdate: ProjectUpdateRequest = {}
      if (typeof data.name === 'string') {
        baseUpdate.name = data.name
      }
      if (typeof data.description === 'string') {
        baseUpdate.description = data.description
      }
      if (data.tags !== undefined) {
        const rawTags = data.tags
        if (Array.isArray(rawTags)) {
          baseUpdate.tags = rawTags.filter((tag): tag is string => typeof tag === 'string')
        } else if (typeof rawTags === 'string') {
          baseUpdate.tags = rawTags
            .split(',')
            .map((tag) => tag.trim())
            .filter(Boolean)
        }
      }
      if (Array.isArray(data.dependencies)) {
        baseUpdate.dependencies = data.dependencies as string[]
      }

      type Step = { name: string; run: () => Promise<void> }
      const steps: Step[] = []

      if (Object.keys(baseUpdate).length > 0) {
        steps.push({
          name: '项目基本信息',
          run: async () => {
            await projectService.updateProject(project.id, baseUpdate)
          },
        })
      }

      if (project.type === 'rule') {
        steps.push({
          name: '规则配置',
          run: async () => {
            await projectService.updateRuleConfig(
              project.id,
              data as Partial<ProjectUpdateRequest>,
            )
          },
        })
      } else if (project.type === 'code') {
        const payload: ProjectCodeConfigUpdateRequest = {
          language: data.language as string | undefined,
          version: data.version as string | undefined,
          entry_point: data.entry_point as string | undefined,
          documentation: data.documentation as string | undefined,
          source_type: data.source_type as ProjectCodeConfigUpdateRequest['source_type'],
          git_url: data.git_url as string | undefined,
          git_branch: data.git_branch as string | undefined,
          git_commit: data.git_commit as string | undefined,
          git_subdir: data.git_subdir as string | undefined,
          git_credential_id: data.git_credential_id as string | undefined,
          code_content: data.code_content as string | undefined,
        }
        steps.push({
          name: '代码配置',
          run: async () => {
            await projectService.updateCodeConfig(project.id, payload)
          },
        })
      } else if (project.type === 'file') {
        const payload: ProjectFileConfigUpdateRequest = {
          entry_point: data.entry_point as string | undefined,
          runtime_config: data.runtime_config as ProjectFileConfigUpdateRequest['runtime_config'],
          environment_vars: data.environment_vars as ProjectFileConfigUpdateRequest['environment_vars'],
          file: data.file as File | undefined,
          source_type: data.source_type as ProjectFileConfigUpdateRequest['source_type'],
          git_url: data.git_url as string | undefined,
          git_branch: data.git_branch as string | undefined,
          git_commit: data.git_commit as string | undefined,
          git_subdir: data.git_subdir as string | undefined,
          git_credential_id: data.git_credential_id as string | undefined,
        }
        steps.push({
          name: '文件配置',
          run: async () => {
            await projectService.updateFileConfig(project.id, payload)
          },
        })
      }

      const failures: string[] = []
      let abort = false

      for (let i = 0; i < steps.length; i += 1) {
        if (abort) break
        const step = steps[i]
        try {
          await step.run()
        } catch (error) {
          const err = error as { message?: string }
          failures.push(step.name)
          // 还有后续步骤时询问是否继续
          if (i < steps.length - 1) {
             
            const shouldContinue = await new Promise<boolean>((resolve) => {
              Modal.confirm({
                title: `${step.name}保存失败`,
                content: `${step.name}保存失败：${err.message ?? '未知错误'}。是否继续后续步骤？`,
                okText: '继续',
                cancelText: '中止',
                onOk: () => resolve(true),
                onCancel: () => resolve(false),
              })
            })
            if (!shouldContinue) abort = true
          } else {
            // 最后一步失败，直接提示
            showNotification('error', `${step.name}保存失败`, err.message)
          }
        }
      }

      if (failures.length === 0) {
        showNotification('success', '项目已保存')
        onSuccess?.()
        onClose()
      } else {
        showNotification(
          'warning',
          '保存部分完成',
          `以下步骤失败：${failures.join('、')}`,
        )
      }
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
        source_type: project.code_info.source_type,
        git_url: project.code_info.git_url,
        git_branch: project.code_info.git_branch,
        git_commit: project.code_info.git_commit,
        git_subdir: project.code_info.git_subdir,
        git_credential_id: project.code_info.git_credential_id,
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
        source_type: project.file_info.source_type,
        git_url: project.file_info.git_url,
        git_branch: project.file_info.git_branch,
        git_commit: project.file_info.git_commit,
        git_subdir: project.file_info.git_subdir,
        git_credential_id: project.file_info.git_credential_id,
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
