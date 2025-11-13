import type { Rule } from 'antd/es/form'

/**
 * 表单验证规则
 */
export const validationRules = {
  // 项目名称验证
  projectName: [
    { required: true, message: '请输入项目名称' },
    { min: 2, max: 50, message: '项目名称长度为2-50个字符' },
    { 
      pattern: /^[a-zA-Z0-9\u4e00-\u9fa5_-]+$/, 
      message: '只能包含字母、数字、中文、下划线和横线' 
    }
  ] as Rule[],

  // URL验证
  url: [
    { required: true, message: '请输入URL' },
    { type: 'url' as const, message: '请输入有效的URL' }
  ] as Rule[],

  // 用户名验证
  username: [
    { required: true, message: '请输入用户名' },
    { min: 3, max: 20, message: '用户名长度为3-20个字符' },
    { 
      pattern: /^[a-zA-Z0-9_]+$/, 
      message: '只能包含字母、数字和下划线' 
    }
  ] as Rule[],

  // 密码验证
  password: [
    { required: true, message: '请输入密码' },
    { min: 4, max: 50, message: '密码长度为4-50个字符' }
  ] as Rule[],

  // 邮箱验证
  email: [
    { type: 'email' as const, message: '请输入有效的邮箱地址' }
  ] as Rule[],

  // 必填项验证
  required: (message: string): Rule[] => [
    { required: true, message }
  ],

  // 数字验证
  number: (min?: number, max?: number): Rule[] => {
    const rules: Rule[] = [
      { type: 'number' as const, message: '请输入有效的数字' }
    ]
    if (min !== undefined) {
      rules.push({ min, message: `数值不能小于${min}` })
    }
    if (max !== undefined) {
      rules.push({ max, message: `数值不能大于${max}` })
    }
    return rules
  },

  // 文件大小验证（字节）
  fileSize: (maxSize: number): Rule => ({
    validator: (_, file) => {
      if (!file || file.size <= maxSize) {
        return Promise.resolve()
      }
      return Promise.reject(new Error(`文件大小不能超过${formatFileSize(maxSize)}`))
    }
  }),

  // 文件类型验证
  fileType: (allowedTypes: string[]): Rule => ({
    validator: (_, file) => {
      if (!file) return Promise.resolve()
      
      const fileType = file.type || ''
      const fileName = file.name || ''
      const fileExtension = fileName.split('.').pop()?.toLowerCase()
      
      const isValidType = allowedTypes.some(type => {
        if (type.startsWith('.')) {
          return fileExtension === type.slice(1)
        }
        return fileType.includes(type)
      })
      
      if (isValidType) {
        return Promise.resolve()
      }
      
      return Promise.reject(new Error(`只支持以下文件类型：${allowedTypes.join(', ')}`))
    }
  })
}

/**
 * 格式化文件大小（用于验证消息）
 */
const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 Bytes'
  
  const k = 1024
  const sizes = ['Bytes', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

/**
 * XSS 防护 - 清理输入
 */
export const sanitizeInput = (input: string): string => {
  return input
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;')
    .replace(/\//g, '&#x2F;')
}

/**
 * 验证JSON格式
 */
export const isValidJSON = (str: string): boolean => {
  try {
    JSON.parse(str)
    return true
  } catch {
    return false
  }
}

/**
 * 验证URL格式
 */
export const isValidURL = (str: string): boolean => {
  try {
    new URL(str)
    return true
  } catch {
    return false
  }
}

/**
 * 验证IP地址
 */
export const isValidIP = (ip: string): boolean => {
  const ipv4Regex = /^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$/
  const ipv6Regex = /^(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$/
  return ipv4Regex.test(ip) || ipv6Regex.test(ip)
}
