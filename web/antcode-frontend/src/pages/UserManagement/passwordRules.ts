import type { Rule } from 'antd/es/form'

const STRONG_PASSWORD = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*(),.?":{}|<>_\-+=[\]\\;'`~]).{8,}$/

export const passwordRules: Rule[] = [
  { required: true, message: '请输入密码' },
  {
    validator: async (_, value: string | undefined) => {
      if (!value || STRONG_PASSWORD.test(value)) return
      throw new Error('密码至少 8 位，并包含大小写字母、数字和特殊字符')
    },
  },
]
