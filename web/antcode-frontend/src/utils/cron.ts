// Cron 表达式验证和解析工具
// 统一使用 5 段标准格式：分 时 日 月 周
// 后端调度器（APScheduler/Croniter）使用 5 段，前端旧的 6 段 Quartz 风格已弃用
//
// 字段范围：
//   分: 0-59
//   时: 0-23
//   日: 1-31
//   月: 1-12
//   周: 0-6 (0=周日)
// 支持: *, A, A-B, A,B,C, */N, A-B/N

/**
 * 验证 Cron 表达式格式（5 段标准）
 */
export function validateCronExpression(expression: string): boolean {
  return validateCron(expression).valid
}

/**
 * 详细验证 Cron 表达式
 */
export function validateCron(expression: string): { valid: boolean; error?: string } {
  if (!expression || typeof expression !== 'string') {
    return { valid: false, error: 'Cron 表达式不能为空' }
  }

  const parts = expression.trim().split(/\s+/)
  if (parts.length !== 5) {
    return { valid: false, error: 'Cron 必须为 5 段：分 时 日 月 周' }
  }

  const ranges: Array<[number, number, string]> = [
    [0, 59, '分'],
    [0, 23, '时'],
    [1, 31, '日'],
    [1, 12, '月'],
    [0, 6, '周'],
  ]

  for (let i = 0; i < 5; i += 1) {
    const [min, max, name] = ranges[i]
    if (!validateField(parts[i], min, max)) {
      return { valid: false, error: `第 ${i + 1} 段（${name}）非法：${parts[i]}` }
    }
  }

  return { valid: true }
}

/**
 * 验证单个字段
 * 支持: *, A, A-B, A,B,C, */N, A-B/N
 */
function validateField(field: string, min: number, max: number): boolean {
  if (!field) return false
  // 通配符
  if (field === '*') return true

  // 步长: */N 或 A-B/N
  if (field.includes('/')) {
    const [range, stepStr] = field.split('/')
    const step = parseInt(stepStr, 10)
    if (!Number.isFinite(step) || step <= 0) return false
    if (range === '*') return true
    if (range.includes('-')) {
      const [a, b] = range.split('-').map(n => parseInt(n, 10))
      return Number.isFinite(a) && Number.isFinite(b) && a >= min && b <= max && a <= b
    }
    const single = parseInt(range, 10)
    return Number.isFinite(single) && single >= min && single <= max
  }

  // 范围: A-B
  if (field.includes('-')) {
    const [a, b] = field.split('-').map(n => parseInt(n, 10))
    return Number.isFinite(a) && Number.isFinite(b) && a >= min && b <= max && a <= b
  }

  // 列表: A,B,C
  if (field.includes(',')) {
    const values = field.split(',').map(v => parseInt(v.trim(), 10))
    return values.every(n => Number.isFinite(n) && n >= min && n <= max)
  }

  // 单值
  const num = parseInt(field, 10)
  return Number.isFinite(num) && num >= min && num <= max
}

/**
 * 解析 Cron 表达式为人类可读的描述
 */
export function describeCronExpression(expression: string): string {
  if (!validateCronExpression(expression)) {
    return '无效的 Cron 表达式'
  }

  const [minute, hour, day, month, week] = expression.trim().split(/\s+/)
  let description = ''

  if (minute === '*') {
    description += '每分钟 '
  } else if (minute.includes('/')) {
    const step = minute.split('/')[1]
    description += `每 ${step} 分钟 `
  } else {
    description += `在第 ${minute} 分钟 `
  }

  if (hour === '*') {
    description += '每小时 '
  } else if (hour.includes('/')) {
    const step = hour.split('/')[1]
    description += `每 ${step} 小时 `
  } else {
    description += `在 ${hour} 点 `
  }

  if (day !== '*') {
    if (day.includes('/')) {
      const step = day.split('/')[1]
      description += `每 ${step} 天 `
    } else {
      description += `在每月第 ${day} 天 `
    }
  }

  if (month !== '*') {
    if (month.includes('/')) {
      const step = month.split('/')[1]
      description += `每 ${step} 个月 `
    } else {
      description += `在 ${month} 月 `
    }
  }

  if (week !== '*') {
    const weekNames = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']
    if (week.includes('/')) {
      const step = week.split('/')[1]
      description += `每 ${step} 周 `
    } else if (week.includes(',')) {
      const days = week.split(',').map(d => weekNames[parseInt(d, 10)] ?? d).join('、')
      description += `在 ${days} `
    } else if (week.includes('-')) {
      const [a, b] = week.split('-').map(n => parseInt(n, 10))
      description += `在 ${weekNames[a] ?? a} 至 ${weekNames[b] ?? b} `
    } else {
      description += `在 ${weekNames[parseInt(week, 10)] ?? week} `
    }
  }

  return description.trim() || '每分钟执行'
}

/**
 * 常用的 Cron 表达式模板（5 段标准）
 */
export const cronTemplates = [
  { label: '每分钟', value: '* * * * *' },
  { label: '每 5 分钟', value: '*/5 * * * *' },
  { label: '每 15 分钟', value: '*/15 * * * *' },
  { label: '每 30 分钟', value: '*/30 * * * *' },
  { label: '每小时', value: '0 * * * *' },
  { label: '每天凌晨', value: '0 0 * * *' },
  { label: '每天上午 9 点', value: '0 9 * * *' },
  { label: '每天中午 12 点', value: '0 12 * * *' },
  { label: '工作日 9 点', value: '0 9 * * 1-5' },
  { label: '每周一', value: '0 0 * * 1' },
  { label: '每月 1 日', value: '0 0 1 * *' },
]

/**
 * 获取下次执行时间（简单占位）
 * 注：精确实现请使用 cron-parser 等库
 */
export function getNextRunTime(expression: string): Date | null {
  if (!validateCronExpression(expression)) {
    return null
  }
  return new Date(Date.now() + 60_000)
}
