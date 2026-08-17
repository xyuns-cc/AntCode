import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

import { createProjectFormData } from './projectPayloads'
import type { ProjectCreateRequest } from '@/types'

/**
 * 前端侧的线格式绑定：`createProjectFormData` 的产出必须逐条等于
 * contracts/http/project_create_form.json 里的 `form_entries`。
 *
 * 后端侧由 tests/unit/web_api/test_project_create_form_wire_contract.py 用同一份
 * `form_entries` 真发 multipart 请求并断言解析结果。两段接起来，任何一侧单方面改
 * 编码方式都必定有一处红——include_paths 此前被整体 JSON.stringify 成一个表单值、
 * 后端却按重复键收集列表，正是因为两侧各自声明且没有可测绑定。
 */

interface WireCase {
  name: string
  request: ProjectCreateRequest
  form_entries: [string, string][]
}

// vitest 的 cwd 固定是 vite 配置根目录 web/antcode-frontend（npm scripts 与 Makefile 都从这里跑）。
const CONTRACT_PATH = resolve(process.cwd(), '../../contracts/http/project_create_form.json')

const contract = JSON.parse(readFileSync(CONTRACT_PATH, 'utf-8')) as { cases: WireCase[] }

const entriesOf = (form: FormData): [string, string][] =>
  Array.from(form.entries()).map(([key, value]) => {
    expect(typeof value, `${key} 必须编码为文本表单字段`).toBe('string')
    return [key, value as string]
  })

describe('project create form wire contract', () => {
  it('covers the empty, non-empty and file-project include_paths shapes', () => {
    expect(contract.cases.map((item) => item.name)).toEqual([
      'code_project_without_shared_paths',
      'code_project_with_shared_paths',
      'file_project_with_shared_paths',
    ])
  })

  it.each(contract.cases)('encodes $name exactly as the shared contract', (wireCase) => {
    expect(entriesOf(createProjectFormData(wireCase.request))).toEqual(wireCase.form_entries)
  })

  it('emits one form entry per include path rather than a single JSON blob', () => {
    const form = createProjectFormData({
      name: 'inline',
      type: 'code',
      runtime_scope: 'private',
      python_version: '3.12',
      repository_id: 'repo-001',
      subdir: 'spiders/news',
      include_paths: ['libs/common', 'libs/utils'],
    })

    expect(form.getAll('include_paths')).toEqual(['libs/common', 'libs/utils'])
    expect(form.getAll('include_paths')).not.toContain('["libs/common","libs/utils"]')
  })

  it('omits include_paths entirely when no shared directory is configured', () => {
    const form = createProjectFormData({
      name: 'inline-empty',
      type: 'code',
      runtime_scope: 'private',
      python_version: '3.12',
      repository_id: 'repo-001',
      subdir: 'spiders/news',
      include_paths: [],
    })

    expect(form.getAll('include_paths')).toEqual([])
    expect(form.has('include_paths')).toBe(false)
  })
})
