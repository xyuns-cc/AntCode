// Cron 表达式验证和解析工具

/**
 * 验证Cron表达式格式
 * 支持6位格式: 秒 分 时 日 月 周
 * 支持5位格式: 分 时 日 月 周
 */
export function validateCronExpression(expression: string): boolean {
  if (!expression || typeof expression !== 'string') {
    return false
  }

  const parts = expression.trim().split(/\s+/)
  
  // 支持5位或6位格式
  if (parts.length !== 5 && parts.length !== 6) {
    return false
  }

  // 验证每个字段
  const validators = parts.length === 6 
    ? [validateSecond, validateMinute, validateHour, validateDay, validateMonth, validateWeek]
    : [validateMinute, validateHour, validateDay, validateMonth, validateWeek]

  return parts.every((part, index) => validators[index](part))
}

/**
 * 验证秒字段 (0-59)
 */
function validateSecond(value: string): boolean {
  return validateField(value, 0, 59)
}

/**
 * 验证分钟字段 (0-59)
 */
function validateMinute(value: string): boolean {
  return validateField(value, 0, 59)
}

/**
 * 验证小时字段 (0-23)
 */
function validateHour(value: string): boolean {
  return validateField(value, 0, 23)
}

/**
 * 验证日字段 (1-31)
 */
function validateDay(value: string): boolean {
  return validateField(value, 1, 31)
}

/**
 * 验证月字段 (1-12)
 */
function validateMonth(value: string): boolean {
  return validateField(value, 1, 12)
}

/**
 * 验证周字段 (0-7, 0和7都表示周日)
 */
function validateWeek(value: string): boolean {
  return validateField(value, 0, 7)
}

/**
 * 验证单个字段
 */
function validateField(value: string, min: number, max: number): boolean {
  // 通配符
  if (value === '*' || value === '?') {
    return true
  }

  // 范围 (例如: 1-5)
  if (value.includes('-')) {
    const [start, end] = value.split('-')
    const startNum = parseInt(start)
    const endNum = parseInt(end)
    return !isNaN(startNum) && !isNaN(endNum) && 
           startNum >= min && endNum <= max && startNum <= endNum
  }

  // 步长 (例如: */5, 1-10/2)
  if (value.includes('/')) {
    const [range, step] = value.split('/')
    const stepNum = parseInt(step)
    if (isNaN(stepNum) || stepNum <= 0) return false
    
    if (range === '*') return true
    if (range.includes('-')) {
      const [start, end] = range.split('-')
      const startNum = parseInt(start)
      const endNum = parseInt(end)
      return !isNaN(startNum) && !isNaN(endNum) && 
             startNum >= min && endNum <= max && startNum <= endNum
    }
    return false
  }

  // 列表 (例如: 1,3,5)
  if (value.includes(',')) {
    const values = value.split(',')
    return values.every(v => {
      const num = parseInt(v.trim())
      return !isNaN(num) && num >= min && num <= max
    })
  }

  // 单个数值
  const num = parseInt(value)
  return !isNaN(num) && num >= min && num <= max
}

/**
 * 解析Cron表达式为人类可读的描述
 */
export function describeCronExpression(expression: string): string {
  if (!validateCronExpression(expression)) {
    return '无效的Cron表达式'
  }

  const parts = expression.trim().split(/\s+/)
  const is6Parts = parts.length === 6
  
  const [second, minute, hour, day, month, week] = is6Parts 
    ? parts 
    : ['0', ...parts]

  let description = ''

  // 构建描述
  if (second !== '0' && second !== '*') {
    description += `在第${second}秒 `
  }

  if (minute === '*') {
    description += '每分钟 '
  } else if (minute.includes('/')) {
    const step = minute.split('/')[1]
    description += `每${step}分钟 `
  } else {
    description += `在第${minute}分钟 `
  }

  if (hour === '*') {
    description += '每小时 '
  } else if (hour.includes('/')) {
    const step = hour.split('/')[1]
    description += `每${step}小时 `
  } else {
    description += `在${hour}点 `
  }

  if (day !== '*' && day !== '?') {
    if (day.includes('/')) {
      const step = day.split('/')[1]
      description += `每${step}天 `
    } else {
      description += `在每月第${day}天 `
    }
  }

  if (month !== '*') {
    if (month.includes('/')) {
      const step = month.split('/')[1]
      description += `每${step}个月 `
    } else {
      description += `在${month}月 `
    }
  }

  if (week !== '*' && week !== '?') {
    const weekNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六', '周日']
    if (week.includes('/')) {
      const step = week.split('/')[1]
      description += `每${step}周 `
    } else if (week.includes(',')) {
      const days = week.split(',').map(d => weekNames[parseInt(d)]).join('、')
      description += `在${days} `
    } else {
      description += `在${weekNames[parseInt(week)]} `
    }
  }

  return description.trim() || '每秒执行'
}

/**
 * 常用的Cron表达式模板
 */
export const cronTemplates = [
  { label: '每分钟', value: '0 * * * * ?' },
  { label: '每5分钟', value: '0 */5 * * * ?' },
  { label: '每15分钟', value: '0 */15 * * * ?' },
  { label: '每30分钟', value: '0 */30 * * * ?' },
  { label: '每小时', value: '0 0 * * * ?' },
  { label: '每天0点', value: '0 0 0 * * ?' },
  { label: '每天12点', value: '0 0 12 * * ?' },
  { label: '每周一9点', value: '0 0 9 ? * MON' },
  { label: '每月1号0点', value: '0 0 0 1 * ?' },
  { label: '工作日9点', value: '0 0 9 ? * MON-FRI' }
]

/**
 * 获取下次执行时间（简单估算）
 */
export function getNextRunTime(expression: string): Date | null {
  // 这里只是一个简单的实现，实际项目中建议使用专业的cron库
  // 如 node-cron 或 cron-parser
  
  if (!validateCronExpression(expression)) {
    return null
  }

  const now = new Date()
  const nextRun = new Date(now.getTime() + 60000) // 简单地加1分钟
  
  return nextRun
}
