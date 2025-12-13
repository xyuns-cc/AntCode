import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { taskService } from '@/services/tasks'
import { projectService } from '@/services/projects'
import type { TaskListParams, TaskListResponse, Project } from '@/types'

export interface UseTasksParams {
  page: number
  size: number
  project_id?: string
  status?: TaskListParams['status']
  schedule_type?: TaskListParams['schedule_type']
  search?: string
  node_id?: string
}

const buildTaskParams = (params: UseTasksParams): TaskListParams => {
  const { page, size, project_id, status, schedule_type, search, node_id } = params
  return {
    page,
    size,
    project_id,
    status,
    schedule_type,
    search,
    node_id
  }
}

export const useTasksQuery = (params: UseTasksParams, enabled: boolean) => {
  return useQuery<TaskListResponse>({
    queryKey: ['tasks', params],
    queryFn: () => taskService.getTasks(buildTaskParams(params)),
    keepPreviousData: true,
    enabled
  })
}

export const useProjectsQuery = (enabled: boolean = true) => {
  return useQuery<{ items: Project[]; page: number; size: number; total: number; pages: number }>({
    queryKey: ['projects', 'options'],
    queryFn: () => projectService.getProjects({ page: 1, size: 200 }),
    staleTime: 60_000,
    enabled
  })
}

export const useTaskMutations = () => {
  const queryClient = useQueryClient()

  const invalidateTasks = () => queryClient.invalidateQueries({ queryKey: ['tasks'] })

  const triggerTask = useMutation({
    mutationFn: (taskId: number | string) => taskService.triggerTask(String(taskId)),
    onSuccess: () => invalidateTasks()
  })

  const deleteTask = useMutation({
    mutationFn: (taskId: number | string) => taskService.deleteTask(String(taskId)),
    onSuccess: () => invalidateTasks()
  })

  const batchDelete = useMutation({
    mutationFn: (ids: (string | number)[]) => taskService.batchDeleteTasks(ids),
    onSuccess: () => invalidateTasks()
  })

  return { triggerTask, deleteTask, batchDelete }
}
