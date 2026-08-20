import type React from 'react'

import { DEFAULT_ICON_SIZE, type IconGlyph } from './iconBase'
import { ConfigIcon, CssIcon, HtmlIcon, ImageIcon, JsonIcon, MarkdownIcon } from './documentIcons'
import { GoIcon, JavaIcon, JavaScriptIcon, PythonIcon, TypeScriptIcon } from './languageIcons'
import { DefaultFileIcon, FolderIcon } from './structuralIcons'

// 键一律带前导点，与 antcode_contracts.execution_language._ENTRY_POINT_SUFFIXES、
// 入口文件提示语（.py/.js/.ts/.jar/.go）和 Node 的 path.extname() 是同一种形状。
// 全仓只有这一张后缀表，且既不在这里剥点也不在调用方剥点：一旦允许两种写法，
// 就又多出一个可分叉的真源，而分叉的代价是静默的——落到兜底图标不会报任何错。
//
// .java 和 .jar 都给 Java 图标：图标回答"这个文件长什么样"，
// 执行契约只收 .jar 回答的是"这个文件能不能跑"，是两个不同的问题。
const ICON_BY_SUFFIX = {
  '.py': PythonIcon,
  '.pyw': PythonIcon,
  '.pyc': PythonIcon,
  '.js': JavaScriptIcon,
  '.jsx': JavaScriptIcon,
  '.mjs': JavaScriptIcon,
  '.cjs': JavaScriptIcon,
  '.ts': TypeScriptIcon,
  '.tsx': TypeScriptIcon,
  '.mts': TypeScriptIcon,
  '.cts': TypeScriptIcon,
  '.java': JavaIcon,
  '.jar': JavaIcon,
  '.go': GoIcon,
  '.json': JsonIcon,
  '.jsonc': JsonIcon,
  '.md': MarkdownIcon,
  '.markdown': MarkdownIcon,
  '.mdown': MarkdownIcon,
  '.css': CssIcon,
  '.scss': CssIcon,
  '.sass': CssIcon,
  '.less': CssIcon,
  '.html': HtmlIcon,
  '.htm': HtmlIcon,
  '.xhtml': HtmlIcon,
  '.yml': ConfigIcon,
  '.yaml': ConfigIcon,
  '.xml': ConfigIcon,
  '.toml': ConfigIcon,
  '.ini': ConfigIcon,
  '.cfg': ConfigIcon,
  '.conf': ConfigIcon,
  '.png': ImageIcon,
  '.jpg': ImageIcon,
  '.jpeg': ImageIcon,
  '.gif': ImageIcon,
  '.webp': ImageIcon,
  '.svg': ImageIcon,
  '.ico': ImageIcon,
  '.bmp': ImageIcon,
} satisfies Record<string, IconGlyph>

/**
 * 图标表收录的后缀。语言下拉这类**固定集合**必须用它标注自己的后缀字段，
 * 写成裸后缀（'py'）或未收录的值都会在 type-check 阶段失败，而不是退化成灰图标。
 */
export type KnownFileSuffix = keyof typeof ICON_BY_SUFFIX

interface FileIconProps {
  /** 带前导点的文件后缀，如 `.py`；取自 path.extname() 或 KnownFileSuffix 常量。 */
  suffix: string
  fileName?: string
  isDirectory?: boolean
  size?: number
  className?: string
}

// 无扩展名但有约定俗成含义的文件；只在后缀查不到时才轮到它们。
const iconByFileName = (fileName: string): IconGlyph | undefined => {
  if (fileName.includes('dockerfile')) {
    return ConfigIcon
  }
  if (fileName.includes('readme') || fileName.includes('license')) {
    return MarkdownIcon
  }
  return undefined
}

const iconBySuffix = (suffix: string): IconGlyph | undefined =>
  Object.hasOwn(ICON_BY_SUFFIX, suffix) ? ICON_BY_SUFFIX[suffix as KnownFileSuffix] : undefined

export const FileIcon: React.FC<FileIconProps> = ({
  suffix,
  fileName = '',
  isDirectory = false,
  size = DEFAULT_ICON_SIZE,
  className = '',
}) => {
  if (isDirectory) {
    return <FolderIcon size={size} className={className} />
  }

  const Glyph = iconBySuffix(suffix.toLowerCase()) ?? iconByFileName(fileName.toLowerCase())

  // 兜底图标只服务于开放输入——任意仓库文件的后缀本就无法穷举，此时通用文件图标
  // 是正确渲染而非掩盖问题。固定集合走 KnownFileSuffix 在编译期挡下，不会再悄悄
  // 落到这里：五种语言图标此前整体失效数月无人察觉，正是因为它们落进了这一支。
  if (Glyph === undefined) {
    return <DefaultFileIcon size={size} className={className} />
  }
  return <Glyph size={size} className={className} />
}
