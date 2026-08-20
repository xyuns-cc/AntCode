import type React from 'react'

import { DEFAULT_ICON_SIZE, type IconGlyph } from './iconBase'

// 目录与"未识别类型"两种图标：它们表达的是条目在树里的结构位置，而不是某种文件格式。
const DEFAULT_FILE_COLOR = '#90a4ae'

// 目录图标
export const FolderIcon: IconGlyph = ({ size = DEFAULT_ICON_SIZE, className = '' }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={className}
    style={{ color: '#42a5f5' }}
  >
    <path
      d="M4 6V18C4 19.1 4.89 20 6 20H18C19.11 20 20 19.1 20 18V8C20 6.9 19.11 6 18 6H12L10 4H6C4.89 4 4 4.9 4 6Z"
      fill="currentColor"
    />
  </svg>
)

// 通用文件图标
export const DefaultFileIcon: React.FC<{
  size?: number
  className?: string
  color?: string
}> = ({ size = DEFAULT_ICON_SIZE, className = '', color = DEFAULT_FILE_COLOR }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={className}
    style={{ color }}
  >
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="currentColor"
      fillOpacity="0.8"
    />
    <path d="M14 2V8H20" stroke="currentColor" strokeWidth="1.5" fill="none" />
  </svg>
)
