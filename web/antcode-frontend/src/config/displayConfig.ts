/**
 * 集中显示配置
 * 用于管理运行时环境作用域等枚举值的显示映射
 */

// ============ 类型定义 ============

/**
 * 显示配置接口
 */
export interface SourceDisplayConfig {
  /** 显示颜色（Ant Design Tag 颜色） */
  color: string
  /** 显示标签 */
  label: string
}

// ============ 运行时环境作用域配置 ============

/**
 * 运行时环境作用域显示配置映射
 */
export const runtimeScopeConfig: Record<string, SourceDisplayConfig> = {
  shared: { color: 'gold', label: '公共' },
  private: { color: 'magenta', label: '私有' },
}

/**
 * 获取运行时环境作用域的显示配置
 * @param scope 作用域值
 * @returns 显示配置
 */
export function getScopeDisplay(scope: string | undefined | null): SourceDisplayConfig {
  if (!scope) {
    throw new Error('运行时环境作用域为空')
  }
  if (Object.hasOwn(runtimeScopeConfig, scope)) {
    return runtimeScopeConfig[scope]
  }
  throw new Error(`未知运行时环境作用域: ${scope}`)
}

/**
 * 运行时环境作用域下拉选项
 */
export const runtimeScopeOptions = [
  { value: 'private', label: '私有（项目专属）' },
  { value: 'shared', label: '公共（共享）' },
]
