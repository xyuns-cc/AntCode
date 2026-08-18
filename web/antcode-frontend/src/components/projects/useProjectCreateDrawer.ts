import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import { globalModal } from '@/hooks/useMessage'
import type { EnvironmentConfig } from '@/components/runtimes/EnvSelector'
import { projectService } from '@/services/projects'
import { workerService } from '@/services/workers'
import type { Project, ProjectCreateRequest, ProjectType, Worker } from '@/types'
import Logger from '@/utils/logger'
import showNotification from '@/utils/notification'
import type {
  ProjectFormController,
  ProjectFormState,
  RuleDispatchConfig,
} from './ProjectCreateDrawerContent'
import {
  buildEnvironmentProjectRequest,
  buildRuleProjectRequest,
  validateEnvironmentConfig,
} from './projectCreateWorkflow'

const useProjectFormState = (): ProjectFormState => {
  const [valid, setValid] = useState(false)
  const [tooltip, setTooltip] = useState('')
  const [controller, setController] = useState<ProjectFormController | null>(null)
  const onValidationChange = useCallback((nextValid: boolean, nextTooltip: string) => {
    setValid(nextValid)
    setTooltip(nextTooltip)
  }, [])
  return {
    valid,
    tooltip,
    controller,
    onValidationChange,
    onControllerChange: setController,
  }
}

export const useProjectFormStates = (): Record<ProjectType, ProjectFormState> => ({
  rule: useProjectFormState(),
  file: useProjectFormState(),
  code: useProjectFormState(),
})

export const useWorkerList = (open: boolean, onError: () => void): Worker[] => {
  const [workers, setWorkers] = useState<Worker[]>([])
  useEffect(() => {
    if (!open) return
    let active = true
    const load = async () => {
      try {
        const items = await workerService.getAllWorkers()
        if (active) setWorkers(items)
      } catch (error) {
        Logger.error('加载 Worker 列表失败:', error)
        onError()
      }
    }
    void load()
    return () => {
      active = false
    }
  }, [onError, open])
  return workers
}

const useProjectCreateNavigation = (
  currentStep: number,
  projectType: ProjectType | null,
  setCurrentStep: Dispatch<SetStateAction<number>>
) => {
  const onNext = useCallback(() => {
    if (currentStep === 0 && !projectType) {
      showNotification('warning', '请选择项目类型')
      return
    }
    setCurrentStep((step) => step + 1)
  }, [currentStep, projectType, setCurrentStep])
  const onPrevious = useCallback(() => setCurrentStep((step) => step - 1), [setCurrentStep])
  return { onNext, onPrevious }
}

export const useProjectCreateFields = () => {
  const [currentStep, setCurrentStep] = useState(0)
  const [projectType, setProjectType] = useState<ProjectType | null>(null)
  const [loading, setLoading] = useState(false)
  const [formData, setFormData] = useState<Partial<ProjectCreateRequest>>({})
  const [envConfig, setEnvConfig] = useState<EnvironmentConfig | null>(null)
  const [regionConfig, setRegionConfig] = useState<RuleDispatchConfig>({})
  const reset = useCallback(() => {
    setCurrentStep(0)
    setProjectType(null)
    setFormData({})
    setLoading(false)
    setEnvConfig(null)
    setRegionConfig({})
  }, [])
  const onTypeSelect = useCallback((type: ProjectType) => {
    setProjectType(type)
    setFormData({ type })
  }, [])
  const onDataChange = useCallback((data: Partial<ProjectCreateRequest>) => {
    setFormData((current) => ({ ...current, ...data }))
  }, [])
  const onRegionChange = useCallback(
    (config: RuleDispatchConfig) => {
      setRegionConfig(
        formData.engine === 'playwright' ? { ...config, require_render: true } : config
      )
    },
    [formData.engine]
  )
  const navigation = useProjectCreateNavigation(currentStep, projectType, setCurrentStep)
  return {
    currentStep,
    projectType,
    loading,
    setLoading,
    formData,
    envConfig,
    setEnvConfig,
    regionConfig,
    reset,
    ...navigation,
    onTypeSelect,
    onDataChange,
    onRegionChange,
  }
}

interface CloseOptions {
  loading: boolean
  projectType: ProjectType | null
  envConfig: EnvironmentConfig | null
  regionConfig: RuleDispatchConfig
  formData: Partial<ProjectCreateRequest>
  reset: () => void
  onClose: () => void
}

export const useProjectCreateClose = (options: CloseOptions) =>
  useCallback(() => {
    if (options.loading) return
    const dirty = Boolean(
      options.projectType ||
      options.envConfig ||
      options.regionConfig.region ||
      options.regionConfig.require_render ||
      Object.keys(options.formData).length > 0
    )
    if (!dirty) {
      options.reset()
      options.onClose()
      return
    }
    globalModal.confirm({
      title: '放弃创建？',
      content: '当前表单已有填写内容，关闭后将丢失。是否继续？',
      okText: '放弃',
      cancelText: '继续编辑',
      okButtonProps: { danger: true },
      onOk: () => {
        options.reset()
        options.onClose()
      },
    })
  }, [options])

/** 创建成功后的收尾：无条件复位并关闭。
 *
 * 不能复用 `useProjectCreateClose`——那是「用户主动关闭」路径，会因为表单有值而弹
 * 「放弃创建？」确认框；提交成功时没有任何东西需要放弃，用户不点确认就永远不复位，
 * 再次打开抽屉会停在第 2 步并保留上一次的全部输入。
 */
export const useProjectCreateComplete = (reset: () => void, onClose: () => void) =>
  useCallback(() => {
    reset()
    onClose()
  }, [onClose, reset])

interface SubmissionOptions {
  projectType: ProjectType | null
  envConfig: EnvironmentConfig | null
  regionConfig: RuleDispatchConfig
  setLoading: (loading: boolean) => void
  onComplete: () => void
  onSuccess?: (project: Project) => void
}

export const useProjectCreateSubmission = (options: SubmissionOptions) =>
  useCallback(
    async (finalData: Record<string, unknown>) => {
      const data = finalData as unknown as ProjectCreateRequest
      const validationError =
        options.projectType === 'rule' ? null : validateEnvironmentConfig(options.envConfig)
      if (validationError) {
        showNotification('error', validationError)
        return
      }
      const isRule = options.projectType === 'rule'
      const request = isRule
        ? buildRuleProjectRequest(data, options.regionConfig)
        : buildEnvironmentProjectRequest(data, options.envConfig!)
      const actionName = isRule ? '创建规则项目' : '创建项目'
      const configuration = isRule ? options.regionConfig : options.envConfig
      options.setLoading(true)
      try {
        Logger.log(`${actionName}:`, data, isRule ? '区域配置:' : '环境配置:', configuration)
        const project = await projectService.createProject(request)
        options.onSuccess?.(project)
        options.onComplete()
      } catch (error) {
        Logger.error(`${actionName}失败:`, error)
        showNotification('error', `${actionName}失败`, (error as Error).message)
      } finally {
        options.setLoading(false)
      }
    },
    [options]
  )
