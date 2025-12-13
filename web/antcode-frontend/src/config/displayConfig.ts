/**
 * 集中显示配置
 * 用于管理解释器来源、虚拟环境作用域等枚举值的显示映射
 * 支持未知值的优雅降级
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

// ============ 解释器来源配置 ============

/**
 * 解释器来源显示配置映射
 */
export const interpreterSourceConfig: Record<string, SourceDisplayConfig> = {
  mise: { color: 'green', label: 'mise' },
  local: { color: 'purple', label: '手动注册' },
  system: { color: 'geekblue', label: '系统' },
  'pyenv-win': { color: 'orange', label: 'pyenv-win' },
}

/**
 * 默认来源显示配置（用于未知值）
 */
export const defaultSourceConfig: SourceDisplayConfig = {
  color: 'default',
  label: '',
}

/**
 * 获取解释器来源的显示配置
 * @param source 来源值
 * @returns 显示配置，未知值返回默认配置并使用原始值作为标签
 */
export function getSourceDisplay(source: string | undefined | null): SourceDisplayConfig {
  if (!source) {
    return defaultSourceConfig
  }
  // 使用 Object.hasOwn 避免原型链污染（如 constructor、valueOf 等）
  if (Object.hasOwn(interpreterSourceConfig, source)) {
    return interpreterSourceConfig[source]
  }
  return { ...defaultSourceConfig, label: source }
}

// ============ 虚拟环境作用域配置 ============

/**
 * 虚拟环境作用域显示配置映射
 */
export const venvScopeConfig: Record<string, SourceDisplayConfig> = {
  shared: { color: 'gold', label: '公共' },
  private: { color: 'magenta', label: '私有' },
}

/**
 * 获取虚拟环境作用域的显示配置
 * @param scope 作用域值
 * @returns 显示配置，未知值返回默认配置并使用原始值作为标签
 */
export function getScopeDisplay(scope: string | undefined | null): SourceDisplayConfig {
  if (!scope) {
    return { color: 'default', label: '未知' }
  }
  // 使用 Object.hasOwn 避免原型链污染（如 constructor、valueOf 等）
  if (Object.hasOwn(venvScopeConfig, scope)) {
    return venvScopeConfig[scope]
  }
  return { color: 'default', label: scope }
}

// ============ 下拉选项配置 ============

/**
 * 解释器来源下拉选项
 */
export const interpreterSourceOptions = [
  { value: 'mise', label: 'mise（推荐）' },
  { value: 'local', label: '本地解释器' },
]

/**
 * 虚拟环境作用域下拉选项
 */
export const venvScopeOptions = [
  { value: 'private', label: '私有（项目专属）' },
  { value: 'shared', label: '公共（共享）' },
]
