import type React from 'react'
import { memo, useCallback } from 'react'
import { App, Drawer } from 'antd'
import type { Project, ProjectType } from '@/types'
import type { ProjectFormState } from './ProjectCreateDrawerContent'
import { ProjectCreateBody, ProjectCreateFooter } from './ProjectCreateDrawerContent'
import {
  useProjectCreateClose,
  useProjectCreateComplete,
  useProjectCreateFields,
  useProjectCreateSubmission,
  useProjectFormStates,
  useWorkerList,
} from './useProjectCreateDrawer'
import styles from './ProjectCreateDrawer.module.css'

interface ProjectCreateDrawerProps {
  open: boolean
  onClose: () => void
  onSuccess?: (project: Project) => void
}

interface ProjectCreateDrawerViewProps extends ProjectCreateDrawerProps {
  fields: ReturnType<typeof useProjectCreateFields>
  formStates: Record<ProjectType, ProjectFormState>
  workerList: ReturnType<typeof useWorkerList>
  handleClose: () => void
  handleSubmit: (data: Record<string, unknown>) => void
}

const ProjectCreateDrawerView: React.FC<ProjectCreateDrawerViewProps> = (props) => (
  <Drawer
    title="创建新项目"
    placement="right"
    width={720}
    open={props.open}
    onClose={props.handleClose}
    maskClosable={false}
    footer={
      <ProjectCreateFooter
        currentStep={props.fields.currentStep}
        projectType={props.fields.projectType}
        loading={props.fields.loading}
        formStates={props.formStates}
        onNext={props.fields.onNext}
        onPrevious={props.fields.onPrevious}
        onClose={props.handleClose}
      />
    }
    styles={{ footer: { textAlign: 'right' } }}
    className={styles.drawer}
  >
    <ProjectCreateBody
      currentStep={props.fields.currentStep}
      projectType={props.fields.projectType}
      formData={props.fields.formData}
      loading={props.fields.loading}
      envConfig={props.fields.envConfig}
      workerList={props.workerList}
      regionConfig={props.fields.regionConfig}
      formStates={props.formStates}
      onTypeSelect={props.fields.onTypeSelect}
      onDataChange={props.fields.onDataChange}
      onSubmit={props.handleSubmit}
      onEnvChange={props.fields.setEnvConfig}
      onRegionChange={props.fields.onRegionChange}
    />
  </Drawer>
)

const ProjectCreateDrawer: React.FC<ProjectCreateDrawerProps> = memo((props) => {
  const { message } = App.useApp()
  const fields = useProjectCreateFields()
  const formStates = useProjectFormStates()
  const showWorkerError = useCallback(() => message.error('加载 Worker 列表失败'), [message])
  const workerList = useWorkerList(props.open, showWorkerError)
  const handleClose = useProjectCreateClose({ ...fields, onClose: props.onClose })
  const handleComplete = useProjectCreateComplete(fields.reset, props.onClose)
  const handleSubmit = useProjectCreateSubmission({
    ...fields,
    onComplete: handleComplete,
    onSuccess: props.onSuccess,
  })
  return (
    <ProjectCreateDrawerView
      {...props}
      fields={fields}
      formStates={formStates}
      workerList={workerList}
      handleClose={handleClose}
      handleSubmit={handleSubmit}
    />
  )
})

export default ProjectCreateDrawer
