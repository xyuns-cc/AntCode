import { describe, expect, it } from 'vitest'

import { parseExecutionParams, parseEnvironmentVars } from './taskJsonFields'

describe('parseExecutionParams', () => {
  it('把空输入当作未填写而不是错误', () => {
    expect(parseExecutionParams(undefined)).toEqual({ ok: true, value: undefined })
    expect(parseExecutionParams('')).toEqual({ ok: true, value: undefined })
  })

  it('接受任意值类型的 JSON 对象', () => {
    expect(parseExecutionParams('{"a":1,"b":{"c":true}}')).toEqual({
      ok: true,
      value: { a: 1, b: { c: true } },
    })
  })

  it('拒绝非法 JSON', () => {
    expect(parseExecutionParams('{')).toEqual({ ok: false, error: '执行参数 JSON 格式错误' })
  })

  it.each(['[1,2]', 'null', '"str"', '42'])('拒绝非对象 JSON: %s', (raw) => {
    expect(parseExecutionParams(raw)).toEqual({ ok: false, error: '执行参数必须是 JSON 对象' })
  })
})

describe('parseEnvironmentVars', () => {
  it('把空输入当作未填写', () => {
    expect(parseEnvironmentVars(undefined)).toEqual({ ok: true, value: undefined })
  })

  it('接受全字符串值的对象', () => {
    expect(parseEnvironmentVars('{"API_URL":"http://x","TOKEN":"t"}')).toEqual({
      ok: true,
      value: { API_URL: 'http://x', TOKEN: 't' },
    })
  })

  it('拒绝非字符串值并指出是哪个键', () => {
    expect(parseEnvironmentVars('{"PORT":8080}')).toEqual({
      ok: false,
      error: '环境变量 PORT 的值必须是字符串',
    })
  })

  it('拒绝非法 JSON 与非对象 JSON', () => {
    expect(parseEnvironmentVars('{')).toEqual({ ok: false, error: '环境变量 JSON 格式错误' })
    expect(parseEnvironmentVars('[]')).toEqual({ ok: false, error: '环境变量必须是 JSON 对象' })
  })
})
