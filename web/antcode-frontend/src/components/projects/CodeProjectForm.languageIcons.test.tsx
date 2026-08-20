import { fireEvent, render, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import CodeProjectForm from './CodeProjectForm'
import { FileIcon } from '@/utils/fileIcons'

vi.mock('@/services/repositories', () => ({
  repositoryService: { list: vi.fn().mockResolvedValue([]) },
}))

// 判据必须落在 <svg> 内部，不能落在整个 option 上：option 里紧挨着图标的语言标签
// <span style={{ color }}> 用的正是同一批品牌色（Python 的 #3776ab、Go 的 #00add8……），
// 拿 option.textContent/innerHTML 去断言颜色，即使图标全是灰兜底也会绿。
//
// 每个指纹都取自该语言图标独有、且不会出现在标签 span 上的部分：
// Python 取黄色方块 #ffd43b（标签色是蓝色 #3776ab），其余四种取 SVG 里的文字徽标。
const LANGUAGE_ICON_FINGERPRINTS = [
  { label: 'Python', markup: '#ffd43b', badge: null },
  { label: 'JavaScript', markup: '#f7df1e', badge: 'JS' },
  { label: 'TypeScript', markup: '#3178c6', badge: 'TS' },
  { label: 'Java', markup: '#ed8b00', badge: 'JAVA' },
  { label: 'Go', markup: '#00add8', badge: 'GO' },
] as const

// 兜底图标 DefaultFileIcon 唯一的填充方式；语言图标全部写死品牌色，绝不用 currentColor。
const FALLBACK_ICON_FINGERPRINT = 'currentColor'

const openLanguageDropdown = async (container: HTMLElement) => {
  const languageItem = [...container.querySelectorAll('.ant-form-item')].find((item) =>
    item.textContent?.includes('编程语言')
  )
  if (!languageItem) {
    throw new Error('未找到"编程语言"表单项')
  }
  fireEvent.mouseDown(languageItem.querySelector('.ant-select-selector')!)
  await waitFor(() => {
    expect(document.querySelectorAll('.ant-select-item-option').length).toBe(
      LANGUAGE_ICON_FINGERPRINTS.length
    )
  })
}

// 按标签 <span> 而不是 option.textContent 定位：图标里的文字徽标也算进 textContent，
// JavaScript 选项整体读出来是 "JSJavaScript"，直接比对 textContent 会找不到选项。
const iconMarkupFor = (label: string): string => {
  const option = [...document.querySelectorAll('.ant-select-item-option')].find(
    (item) => item.querySelector('span')?.textContent?.trim() === label
  )
  if (!option) {
    throw new Error(`语言下拉里没有 ${label} 选项`)
  }
  const svg = option.querySelector('svg')
  if (!svg) {
    throw new Error(`${label} 选项没有渲染任何图标`)
  }
  return svg.outerHTML
}

describe('语言下拉的文件图标', () => {
  // 证伪项：LANGUAGE_OPTIONS 传的是带点后缀（.py/.jar/...），图标表若按裸后缀匹配，
  // 五个选项会整体落到灰兜底图标上——五条断言同时红。
  it.each(LANGUAGE_ICON_FINGERPRINTS)(
    '$label 选项渲染自己的语言图标而不是兜底图标',
    async ({ label, markup, badge }) => {
      const { container } = render(<CodeProjectForm onSubmit={vi.fn()} />)
      await openLanguageDropdown(container)

      const iconMarkup = iconMarkupFor(label)
      expect(iconMarkup).toContain(markup)
      if (badge) {
        expect(iconMarkup).toContain(`>${badge}</text>`)
      }
      expect(iconMarkup).not.toContain(FALLBACK_ICON_FINGERPRINT)
    }
  )

  // 证伪项：Java 走 .jar，而 .jar 此前连裸后缀形态都没有收录（图标表里只有 java），
  // 只回退图标表里的 '.jar' 这一行，这条会单独红。
  //
  // 光断言"选项图标 === FileIcon('.jar') 的图标"是假绿：两边同时落到兜底图标时它照样成立。
  // 必须再钉住这个共同结果确实是 Java 图标，等式才有判别力。
  it('Java 选项走的是 .jar 而不是 .java', async () => {
    const { container } = render(<CodeProjectForm onSubmit={vi.fn()} />)
    await openLanguageDropdown(container)

    const optionMarkup = iconMarkupFor('Java')
    const { outerHTML } = render(<FileIcon suffix=".jar" />).container.querySelector('svg')!
    expect(optionMarkup).toBe(outerHTML)
    expect(optionMarkup).toContain('>JAVA</text>')
  })
})

describe('FileIcon 对开放输入的兜底', () => {
  // 非证伪项：回退修复后这两条依然绿（修复前后 .rb 都落兜底、目录都是文件夹图标）。
  // 保留是为了钉住"兜底是有意设计"这个决定——任意仓库文件的后缀无法穷举，
  // 未收录时渲染通用文件图标是正确行为，不是掩盖问题。
  it('未收录的后缀渲染通用文件图标', () => {
    const { container } = render(<FileIcon suffix=".rb" />)
    expect(container.querySelector('svg')!.outerHTML).toContain(FALLBACK_ICON_FINGERPRINT)
  })

  it('目录渲染文件夹图标', () => {
    const { container } = render(<FileIcon suffix="" isDirectory />)
    expect(container.querySelector('svg')!.getAttribute('style')).toContain('rgb(66, 165, 245)')
  })
})
