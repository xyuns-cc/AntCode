import type React from 'react'
import { Button, Card } from 'antd'
import { SaveOutlined } from '@ant-design/icons'
import type { Project, ProjectType } from '@/types'
import CodeProjectForm from './CodeProjectForm'
import FileProjectForm from './FileProjectForm'
import RegionWorkerSelector from './RegionWorkerSelector'
import RuleProjectForm from './RuleProjectForm'
import type { ProjectFormController, RuleDispatchConfig } from './ProjectCreateDrawerContent'
import { getProjectInitialData } from './projectEditWorkflow'
import styles from './ProjectEditDrawer.module.css'

export interface ProjectEditFormState {
  valid: boolean
  tooltip: string
  controller: ProjectFormController | null
  onValidationChange: (valid: boolean, tooltip: string) => void
  onControllerChange: (controller: ProjectFormController | null) => void
}

interface ProjectEditContentProps {
  project: Project
  loading: boolean
  ruleDispatchConfig: RuleDispatchConfig
  formStates: Record<ProjectType, ProjectEditFormState>
  onRuleDispatchChange: (config: RuleDispatchConfig) => void
  onSubmit: (data: Record<string, unknown>) => void
}

export const ProjectEditContent: React.FC<ProjectEditContentProps> = ({
  project,
  loading,
  ruleDispatchConfig,
  formStates,
  onRuleDispatchChange,
  onSubmit,
}) => {
  const state = formStates[project.type]
  const commonProps = {
    initialData: getProjectInitialData(project),
    onSubmit,
    loading,
    isEdit: true,
    onValidationChange: state.onValidationChange,
    onRef: state.onControllerChange,
  }
  if (project.type === 'rule') {
    return (
      <>
        <Card title="执行区域配置" size="small" style={{ marginBottom: 16 }}>
          <RegionWorkerSelector
            value={ruleDispatchConfig}
            onChange={onRuleDispatchChange}
            requireRender={project.rule_info?.engine === 'playwright'}
          />
        </Card>
        <RuleProjectForm {...commonProps} />
      </>
    )
  }
  if (project.type === 'code') return <CodeProjectForm {...commonProps} />
  return <FileProjectForm {...commonProps} />
}

interface ProjectEditFooterProps {
  projectType: ProjectType
  loading: boolean
  formStates: Record<ProjectType, ProjectEditFormState>
  onClose: () => void
}

export const ProjectEditFooter: React.FC<ProjectEditFooterProps> = ({
  projectType,
  loading,
  formStates,
  onClose,
}) => {
  const state = formStates[projectType]
  return (
    <div className={styles.footer}>
      <Button onClick={onClose}>取消</Button>
      <Button
        type="primary"
        loading={loading}
        disabled={!state.valid}
        onClick={() => state.controller?.submit()}
        icon={<SaveOutlined />}
        title={state.tooltip}
      >
        保存修改
      </Button>
    </div>
  )
}
