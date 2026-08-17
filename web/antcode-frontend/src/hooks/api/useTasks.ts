import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { taskService } from '@/services/tasks'
import { projectService } from '@/services/projects'
import type {
  TaskListParams,
  TaskListResponse,
  Project,
  TaskCreateRequest,
  TaskUpdateRequest,
} from '@/types'

export interface UseTasksParams {
  page: number
  size: number
  project_id?: string
  status?: TaskListParams['status']
  schedule_type?: TaskListParams['schedule_type']
  search?: string
  specified_worker_id?: string
  worker_id?: string
}

const buildTaskParams = (params: UseTasksParams): TaskListParams => {
  const { page, size, project_id, status, schedule_type, search, specified_worker_id, worker_id } =
    params
  return {
    page,
    size,
    project_id,
    status,
    schedule_type,
    search,
    specified_worker_id,
    worker_id,
  }
}

export const useTasksQuery = (params: UseTasksParams, enabled: boolean) => {
  return useQuery<TaskListResponse>({
    queryKey: ['tasks', params],
    queryFn: () => taskService.getTasks(buildTaskParams(params)),
    placeholderData: (previous) => previous,
    enabled,
  })
}

export const useProjectsQuery = (workerId?: string, enabled: boolean = true) => {
  return useQuery<Project[]>({
    queryKey: ['projects', 'options', workerId || 'all'],
    queryFn: () => projectService.getAllProjects({ worker_id: workerId }),
    staleTime: 60_000,
    enabled,
  })
}

/**
 * 任务列表查询继承全局 staleTime(30s)。任何写操作后都必须失效 ['tasks']，
 * 否则 30s 内跳回列表会直接命中旧缓存，新建/修改的结果看不见（真机复现）。
 */
const useInvalidateTasks = () => {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ['tasks'] })
}

export const useCreateTask = () => {
  const invalidateTasks = useInvalidateTasks()
  return useMutation({
    mutationFn: (payload: TaskCreateRequest) => taskService.createTask(payload),
    onSuccess: () => invalidateTasks(),
  })
}

export const useUpdateTask = () => {
  const invalidateTasks = useInvalidateTasks()
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: TaskUpdateRequest }) =>
      taskService.updateTask(id, payload),
    onSuccess: () => invalidateTasks(),
  })
}

export const useTaskMutations = () => {
  const invalidateTasks = useInvalidateTasks()

  const triggerTask = useMutation({
    mutationFn: (taskId: string) => taskService.triggerTask(taskId),
    onSuccess: () => invalidateTasks(),
  })

  const deleteTask = useMutation({
    mutationFn: (taskId: string) => taskService.deleteTask(taskId),
    onSuccess: () => invalidateTasks(),
  })

  const batchDelete = useMutation({
    mutationFn: (ids: string[]) => taskService.batchDeleteTasks(ids),
    onSuccess: () => invalidateTasks(),
  })

  return { triggerTask, deleteTask, batchDelete }
}
