/**
 * 节点服务 - 管理分布式节点的 API 调用
 * 继承 BaseService 以复用统一的 HTTP 请求方法
 */
import { BaseService } from './base'
import type {
  Node,
  NodeCreateRequest,
  NodeUpdateRequest,
  NodeListParams,
  NodeListResponse,
  NodeAggregateStats
} from '@/types'

class NodeService extends BaseService {
  constructor() {
    super('/api/v1/nodes')
  }

  /**
   * 获取节点列表
   */
  async getNodes(params?: NodeListParams): Promise<NodeListResponse> {
    return this.get<NodeListResponse>('', { params })
  }

  /**
   * 获取所有节点（不分页，用于选择器）
   */
  async getAllNodes(): Promise<Node[]> {
    const result = await this.get<NodeListResponse>('', {
      params: { page: 1, size: 100 }
    })
    return result?.items || []
  }

  /**
   * 获取节点详情
   */
  async getNode(nodeId: string): Promise<Node> {
    return this.get<Node>(`/${nodeId}`)
  }

  /**
   * 创建节点
   */
  async createNode(data: NodeCreateRequest): Promise<Node> {
    return this.post<Node>('', data)
  }

  /**
   * 更新节点
   */
  async updateNode(nodeId: string, data: NodeUpdateRequest): Promise<Node> {
    return this.put<Node>(`/${nodeId}`, data)
  }


  /**
   * 删除节点
   */
  async deleteNode(nodeId: string): Promise<void> {
    await this.delete(`/${nodeId}`)
  }

  /**
   * 批量删除节点
   */
  async batchDeleteNodes(nodeIds: string[]): Promise<{ success_count: number; failed_count: number }> {
    return this.post<{ success_count: number; failed_count: number }>(
      '/batch-delete',
      { node_ids: nodeIds }
    )
  }

  /**
   * 获取节点聚合统计
   */
  async getAggregateStats(): Promise<NodeAggregateStats> {
    return this.get<NodeAggregateStats>('/stats')
  }

  /**
   * 测试节点连接
   */
  async testConnection(nodeId: string): Promise<{ success: boolean; latency?: number; error?: string }> {
    return this.post<{ success: boolean; latency?: number; error?: string }>(`/${nodeId}/test`)
  }

  /**
   * 重新绑定节点机器码
   * 当节点重启后机器码变化时使用，无需删除重建节点
   */
  async rebindNode(nodeId: string, data: { new_machine_code: string; verify_connection?: boolean }): Promise<Node> {
    return this.post<Node>(`/${nodeId}/rebind`, data)
  }

  /**
   * 连接节点（通过地址和机器码）
   */
  async connectNode(data: { host: string; port: number; machine_code: string }): Promise<Node> {
    return this.post<Node>('/connect', data)
  }

  /**
   * 断开节点连接
   */
  async disconnectNode(nodeId: string): Promise<void> {
    await this.post(`/${nodeId}/disconnect`)
  }

  /**
   * 刷新节点状态
   */
  async refreshNodeStatus(nodeId: string): Promise<Node> {
    return this.post<Node>(`/${nodeId}/refresh`)
  }

  /**
   * 获取节点历史指标
   */
  async getNodeMetricsHistory(nodeId: string, hours: number = 24): Promise<Array<{
    timestamp: string
    cpu: number
    memory: number
    disk: number
    taskCount: number
    runningTasks: number
    uptime: number
  }>> {
    const result = await this.get<Array<{
      timestamp: string
      cpu: number
      memory: number
      disk: number
      taskCount: number
      runningTasks: number
      uptime: number
    }>>(`/${nodeId}/metrics/history`, { params: { hours } })
    return result || []
  }

  /**
   * 获取集群历史指标
   */
  async getClusterMetricsHistory(hours: number = 24): Promise<{
    timestamps: string[]
    cpu: { avg: number[]; max: number[]; min: number[] }
    memory: { avg: number[]; max: number[]; min: number[] }
  }> {
    const result = await this.get<{
      timestamps: string[]
      cpu: { avg: number[]; max: number[]; min: number[] }
      memory: { avg: number[]; max: number[]; min: number[] }
    }>('/cluster/metrics/history', { params: { hours } })
    return result || { timestamps: [], cpu: { avg: [], max: [], min: [] }, memory: { avg: [], max: [], min: [] } }
  }

  // ========== 节点权限管理 ==========

  /**
   * 获取当前用户可用的节点列表
   */
  async getMyAvailableNodes(): Promise<Node[]> {
    const result = await this.get<NodeListResponse>('/my/available')
    return result?.items || []
  }

  /**
   * 获取节点的授权用户列表
   */
  async getNodeUsers(nodeId: string): Promise<Array<{
    user_id: string
    username: string
    permission: string
    assigned_at: string
    note?: string
  }>> {
    const result = await this.get<Array<{
      user_id: string
      username: string
      permission: string
      assigned_at: string
      note?: string
    }>>(`/${nodeId}/users`)
    return result || []
  }

  /**
   * 分配节点权限给用户
   */
  async assignNodeToUser(nodeId: string, userId: string | number, permission: string = 'use', note?: string): Promise<void> {
    await this.post(`/${nodeId}/assign`, {
      user_id: userId,
      permission,
      note
    })
  }

  /**
   * 撤销用户的节点权限
   */
  async revokeNodeFromUser(nodeId: string, userId: string | number): Promise<void> {
    await this.delete(`/${nodeId}/revoke/${userId}`)
  }

  /**
   * 批量分配节点权限
   */
  async batchAssignNodes(userId: string | number, nodeIds: string[], permission: string = 'use'): Promise<{
    success: number
    failed: number
  }> {
    return this.post<{ success: number; failed: number }>('/batch-assign', {
      user_id: userId,
      node_ids: nodeIds,
      permission
    })
  }


  // ========== 负载均衡与分布式任务 ==========

  /**
   * 获取节点负载排名
   */
  async getNodesLoadRanking(params?: {
    region?: string
    tags?: string[]
    top_n?: number
  }): Promise<Array<{
    node_id: string
    node_name: string
    status: string
    load_score: number
    metrics: {
      cpu: number
      memory: number
      runningTasks: number
      maxConcurrentTasks: number
    }
  }>> {
    const result = await this.get<Array<{
      node_id: string
      node_name: string
      status: string
      load_score: number
      metrics: {
        cpu: number
        memory: number
        runningTasks: number
        maxConcurrentTasks: number
      }
    }>>('/load/ranking', { params })
    return result || []
  }

  /**
   * 获取最佳节点（负载最低的可用节点）
   */
  async getBestNode(params?: {
    region?: string
    tags?: string
  }): Promise<{
    available: boolean
    node?: Node
    load_score?: number
    message?: string
  }> {
    return this.get<{
      available: boolean
      node?: Node
      load_score?: number
      message?: string
    }>('/best', { params })
  }

  /**
   * 分发任务到节点
   */
  async dispatchTask(data: {
    project_id: string
    task_params?: Record<string, unknown>
    environment_vars?: Record<string, string>
    timeout?: number
    node_id?: string
    region?: string
  }): Promise<{
    success: boolean
    execution_id?: string
    node_id?: string
    node_name?: string
    message?: string
  }> {
    return this.post<{
      success: boolean
      execution_id?: string
      node_id?: string
      node_name?: string
      message?: string
    }>('/dispatch/task', data)
  }

  /**
   * 获取分布式任务日志
   */
  async getDistributedLogs(executionId: string, params?: {
    log_type?: 'stdout' | 'stderr'
    tail?: number
  }): Promise<{
    execution_id: string
    log_type: string
    logs: string[]
    total: number
  }> {
    return this.get<{
      execution_id: string
      log_type: string
      logs: string[]
      total: number
    }>(`/distributed-logs/${executionId}`, { params })
  }

  /**
   * 获取远程节点任务状态
   */
  async getRemoteTaskStatus(nodeId: string, taskId: string): Promise<{
    status: string
    exit_code?: number
    error_message?: string
  }> {
    return this.get<{
      status: string
      exit_code?: number
      error_message?: string
    }>(`/dispatch/task/${nodeId}/${taskId}/status`)
  }

  /**
   * 获取远程节点任务日志
   */
  async getRemoteTaskLogs(nodeId: string, taskId: string, params?: {
    tail?: number
  }): Promise<{
    logs: string[]
    total: number
  }> {
    return this.get<{
      logs: string[]
      total: number
    }>(`/dispatch/task/${nodeId}/${taskId}/logs`, { params })
  }

  // ========== 节点环境管理（用于项目创建）==========

  /**
   * 获取节点可用环境列表
   */
  async getNodeAvailableEnvs(nodeId: string, scope?: string): Promise<{
    envs: Array<{
      name: string
      scope: string
      python_version: string
      description: string
      packages_count: number
      created_at: string
      path: string
    }>
    total: number
  }> {
    return this.get<{
      envs: Array<{
        name: string
        scope: string
        python_version: string
        description: string
        packages_count: number
        created_at: string
        path: string
      }>
      total: number
    }>(`/${nodeId}/envs/available`, {
      params: scope ? { scope } : undefined
    })
  }

  /**
   * 为项目创建节点环境
   */
  async createNodeEnvForProject(nodeId: string, data: {
    name?: string
    scope: 'private' | 'public'
    python_version: string
    description?: string
    packages?: string[]
  }): Promise<{
    env_name: string
    path: string
    python_version: string
    scope: string
    description: string
  }> {
    return this.post<{
      env_name: string
      path: string
      python_version: string
      scope: string
      description: string
    }>(`/${nodeId}/envs/create-for-project`, data)
  }

  // ========== 节点资源管理（管理员功能）==========

  /**
   * 获取节点资源限制（管理员可查看）
   */
  async getNodeResources(nodeId: string): Promise<{
    limits: {
      max_concurrent_tasks: number
      task_memory_limit_mb: number
      task_cpu_time_limit_sec: number
      task_timeout: number
    }
    auto_adjustment: boolean
    resource_stats: {
      cpu_percent: number
      memory_percent: number
      memory_available_gb: number
      memory_total_gb: number
      cpu_history_avg: number
      memory_history_avg: number
      current_limits: {
        max_concurrent_tasks: number
        task_memory_limit_mb: number
        task_cpu_time_limit_sec: number
      }
      auto_adjustment_enabled: boolean
      monitoring_active: boolean
    }
  }> {
    return this.get<{
      limits: {
        max_concurrent_tasks: number
        task_memory_limit_mb: number
        task_cpu_time_limit_sec: number
        task_timeout: number
      }
      auto_adjustment: boolean
      resource_stats: {
        cpu_percent: number
        memory_percent: number
        memory_available_gb: number
        memory_total_gb: number
        cpu_history_avg: number
        memory_history_avg: number
        current_limits: {
          max_concurrent_tasks: number
          task_memory_limit_mb: number
          task_cpu_time_limit_sec: number
        }
        auto_adjustment_enabled: boolean
        monitoring_active: boolean
      }
    }>(`/${nodeId}/resources`)
  }

  /**
   * 调整节点资源限制（仅超级管理员）
   */
  async updateNodeResources(nodeId: string, data: {
    max_concurrent_tasks?: number
    task_memory_limit_mb?: number
    task_cpu_time_limit_sec?: number
    auto_resource_limit?: boolean
  }): Promise<{
    success: boolean
    updated: Record<string, unknown>
    current_limits: {
      max_concurrent_tasks: number
      task_memory_limit_mb: number
      task_cpu_time_limit_sec: number
      auto_resource_limit: boolean
    }
  }> {
    return this.post<{
      success: boolean
      updated: Record<string, unknown>
      current_limits: {
        max_concurrent_tasks: number
        task_memory_limit_mb: number
        task_cpu_time_limit_sec: number
        auto_resource_limit: boolean
      }
    }>(`/${nodeId}/resources`, data)
  }
}

export const nodeService = new NodeService()
export default nodeService
