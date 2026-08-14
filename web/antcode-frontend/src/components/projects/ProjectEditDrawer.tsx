import type React from 'react'
import { useCallback, useEffect, useState } from 'react'
import { Drawer, Space } from 'antd'
import { CloseOutlined } from '@ant-design/icons'
import type { Project, ProjectType } from '@/types'
import showNotification from '@/utils/notification'
import {
  ProjectEditContent,
  ProjectEditFooter,
  type ProjectEditFormState,
} from './ProjectEditDrawerContent'
import type { ProjectFormController, RuleDispatchConfig } from './ProjectCreateDrawerContent'
import { buildProjectUpdateSteps, executeProjectUpdateSteps } from './projectEditWorkflow'
import styles from './ProjectEditDrawer.module.css'

interface ProjectEditDrawerProps {
  open: boolean
  onClose: () => void
  project: Project | null
  onSuccess?: () => void
}

interface FormStateValues {
  controller: ProjectFormController | null
  valid: boolean
  tooltip: string
}

const INITIAL_FORM_STATE: FormStateValues = { controller: null, valid: false, tooltip: '' }

const useProjectEditFormState = () => {
  const [values, setValues] = useState(INITIAL_FORM_STATE)
  const reset = useCallback(() => setValues(INITIAL_FORM_STATE), [])
  const onValidationChange = useCallback((valid: boolean, tooltip: string) => {
    setValues((current) => {
      if (current.valid === valid && current.tooltip === tooltip) return current
      return { ...current, valid, tooltip }
    })
  }, [])
  const onControllerChange = useCallback((controller: ProjectFormController | null) => {
    setValues((current) => ({ ...current, controller }))
  }, [])
  return { reset, state: { ...values, onValidationChange, onControllerChange } }
}

const useProjectEditForms = (open: boolean): Record<ProjectType, ProjectEditFormState> => {
  const { reset: resetRule, state: rule } = useProjectEditFormState()
  const { reset: resetCode, state: code } = useProjectEditFormState()
  const { reset: resetFile, state: file } = useProjectEditFormState()
  useEffect(() => {
    if (open) return
    resetRule()
    resetCode()
    resetFile()
  }, [open, resetCode, resetFile, resetRule])
  return { rule, code, file }
}

const useRuleDispatchConfig = (open: boolean, project: Project | null) => {
  const [config, setConfig] = useState<RuleDispatchConfig>({})
  useEffect(() => {
    if (!open || project?.type !== 'rule') return
    setConfig({
      region: project.rule_info?.region,
      require_render: project.rule_info?.require_render,
    })
  }, [open, project])
  return { config, setConfig }
}

interface SubmissionOptions extends ProjectEditDrawerProps {
  ruleDispatchConfig: RuleDispatchConfig
}

const useProjectEditSubmission = (options: SubmissionOptions) => {
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!options.open) setLoading(false)
  }, [options.open])
  const handleSubmit = useCallback(
    async (data: Record<string, unknown>) => {
      if (!options.project) return
      setLoading(true)
      try {
        const steps = buildProjectUpdateSteps(options.project, data, options.ruleDispatchConfig)
        const failures = await executeProjectUpdateSteps(steps)
        if (failures.length > 0) {
          showNotification('warning', '保存部分完成', `以下步骤失败：${failures.join('、')}`)
          return
        }
        showNotification('success', '项目已保存')
        options.onSuccess?.()
        options.onClose()
      } finally {
        setLoading(false)
      }
    },
    [options]
  )
  return { loading, handleSubmit }
}

interface ProjectEditDrawerViewProps extends ProjectEditDrawerProps {
  project: Project
  loading: boolean
  ruleDispatchConfig: RuleDispatchConfig
  formStates: Record<ProjectType, ProjectEditFormState>
  onRuleDispatchChange: (config: RuleDispatchConfig) => void
  onSubmit: (data: Record<string, unknown>) => void
}

const ProjectEditDrawerView: React.FC<ProjectEditDrawerViewProps> = (props) => (
  <Drawer
    title={
      <Space>
        <span>编辑项目 - {props.project.name}</span>
      </Space>
    }
    width={800}
    open={props.open}
    onClose={props.onClose}
    closeIcon={<CloseOutlined />}
    footer={
      <ProjectEditFooter
        projectType={props.project.type}
        loading={props.loading}
        formStates={props.formStates}
        onClose={props.onClose}
      />
    }
    destroyOnHidden
    className={styles.drawer}
  >
    <div className={styles.content}>
      <ProjectEditContent
        project={props.project}
        loading={props.loading}
        ruleDispatchConfig={props.ruleDispatchConfig}
        formStates={props.formStates}
        onRuleDispatchChange={props.onRuleDispatchChange}
        onSubmit={props.onSubmit}
      />
    </div>
  </Drawer>
)

const ProjectEditDrawer: React.FC<ProjectEditDrawerProps> = (props) => {
  const formStates = useProjectEditForms(props.open)
  const dispatch = useRuleDispatchConfig(props.open, props.project)
  const submission = useProjectEditSubmission({ ...props, ruleDispatchConfig: dispatch.config })
  if (!props.project) return null
  return (
    <ProjectEditDrawerView
      {...props}
      project={props.project}
      loading={submission.loading}
      ruleDispatchConfig={dispatch.config}
      formStates={formStates}
      onRuleDispatchChange={dispatch.setConfig}
      onSubmit={submission.handleSubmit}
    />
  )
}

export default ProjectEditDrawer
