import type React from 'react'
import { Button, Card, Space, Steps } from 'antd'
import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  CodeOutlined,
  FileOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import type { EnvironmentConfig } from '@/components/runtimes/EnvSelector'
import EnvSelector from '@/components/runtimes/EnvSelector'
import type { ProjectCreateRequest, ProjectType, Worker } from '@/types'
import CodeProjectForm from './CodeProjectForm'
import FileProjectForm from './FileProjectForm'
import ProjectTypeSelector from './ProjectTypeSelector'
import RegionWorkerSelector from './RegionWorkerSelector'
import RuleProjectForm from './RuleProjectForm'
import styles from './ProjectCreateDrawer.module.css'

export interface ProjectFormController {
  submit: () => void
}

export interface ProjectFormState {
  valid: boolean
  tooltip: string
  controller: ProjectFormController | null
  onValidationChange: (valid: boolean, tooltip: string) => void
  onControllerChange: (controller: ProjectFormController | null) => void
}

export interface RuleDispatchConfig {
  region?: string
  require_render?: boolean
}

interface ProjectConfigurationProps {
  projectType: ProjectType
  formData: Partial<ProjectCreateRequest>
  loading: boolean
  envConfig: EnvironmentConfig | null
  workerList: Worker[]
  regionConfig: RuleDispatchConfig
  formStates: Record<ProjectType, ProjectFormState>
  onDataChange: (data: Partial<ProjectCreateRequest>) => void
  onSubmit: (data: Record<string, unknown>) => void
  onEnvChange: (config: EnvironmentConfig | null) => void
  onRegionChange: (config: RuleDispatchConfig) => void
}

const ProjectConfiguration: React.FC<ProjectConfigurationProps> = (props) => {
  const commonProps = {
    initialData: props.formData,
    onDataChange: props.onDataChange,
    onSubmit: props.onSubmit,
    loading: props.loading,
  }
  const state = props.formStates[props.projectType]
  const validationProps = {
    onValidationChange: state.onValidationChange,
    onRef: state.onControllerChange,
  }
  if (props.projectType === 'rule') {
    return (
      <>
        <Card title="执行区域配置" size="small" style={{ marginBottom: 16 }}>
          <RegionWorkerSelector
            value={props.regionConfig}
            onChange={props.onRegionChange}
            requireRender={props.formData.engine === 'playwright'}
          />
        </Card>
        <RuleProjectForm {...commonProps} {...validationProps} />
      </>
    )
  }
  const envSection = (
    <EnvSelector
      value={props.envConfig}
      onChange={props.onEnvChange}
      workerList={props.workerList}
    />
  )
  const projectForm =
    props.projectType === 'file' ? (
      <FileProjectForm {...commonProps} {...validationProps} />
    ) : (
      <CodeProjectForm {...commonProps} {...validationProps} />
    )
  return (
    <>
      {envSection}
      {projectForm}
    </>
  )
}

interface ProjectCreateBodyProps extends Omit<ProjectConfigurationProps, 'projectType'> {
  currentStep: number
  projectType: ProjectType | null
  onTypeSelect: (type: ProjectType) => void
}

const projectTypeIcon = (projectType: ProjectType | null) => {
  if (projectType === 'file') return <FileOutlined />
  if (projectType === 'rule') return <SettingOutlined />
  return <CodeOutlined />
}

export const ProjectCreateBody: React.FC<ProjectCreateBodyProps> = (props) => {
  const steps = [
    { title: '选择类型', description: '选择项目类型', icon: <SettingOutlined /> },
    { title: '配置项目', description: '填写项目信息', icon: projectTypeIcon(props.projectType) },
  ]
  let content: React.ReactNode = (
    <ProjectTypeSelector selectedType={props.projectType} onSelect={props.onTypeSelect} />
  )
  if (props.currentStep === 1 && props.projectType) {
    content = <ProjectConfiguration {...props} projectType={props.projectType} />
  }
  return (
    <>
      <div className={styles.steps}>
        <Steps current={props.currentStep} items={steps} size="small" />
      </div>
      <Card variant="borderless" className={styles.formCard}>
        {content}
      </Card>
    </>
  )
}

interface ProjectCreateFooterProps {
  currentStep: number
  projectType: ProjectType | null
  loading: boolean
  formStates: Record<ProjectType, ProjectFormState>
  onNext: () => void
  onPrevious: () => void
  onClose: () => void
}

const CREATE_LABELS: Record<ProjectType, string> = {
  file: '创建文件项目',
  rule: '创建规则项目',
  code: '创建代码项目',
}

export const ProjectCreateFooter: React.FC<ProjectCreateFooterProps> = ({
  currentStep,
  projectType,
  loading,
  formStates,
  onNext,
  onPrevious,
  onClose,
}) => {
  const formState = projectType ? formStates[projectType] : null
  return (
    <Space>
      {currentStep > 0 && (
        <Button icon={<ArrowLeftOutlined />} onClick={onPrevious} disabled={loading}>
          上一步
        </Button>
      )}
      {currentStep === 0 && (
        <Button
          type="primary"
          icon={<ArrowRightOutlined />}
          onClick={onNext}
          disabled={!projectType}
        >
          下一步
        </Button>
      )}
      {currentStep === 1 && projectType && formState && (
        <Button
          type="primary"
          loading={loading}
          disabled={!formState.valid}
          title={formState.tooltip}
          onClick={() => formState.controller?.submit()}
        >
          {CREATE_LABELS[projectType]}
        </Button>
      )}
      <Button onClick={onClose} disabled={loading}>
        取消
      </Button>
    </Space>
  )
}
