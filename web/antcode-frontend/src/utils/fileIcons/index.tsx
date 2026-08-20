/* eslint-disable react-refresh/only-export-components */
import type React from 'react'

import { DEFAULT_ICON_SIZE } from './iconBase'
import {
  ConfigIcon,
  CssIcon,
  HtmlIcon,
  ImageIcon,
  JsonIcon,
  MarkdownIcon,
} from './documentIcons'
import { GoIcon, JavaIcon, JavaScriptIcon, PythonIcon, TypeScriptIcon } from './languageIcons'
import { DefaultFileIcon, FolderIcon } from './structuralIcons'

// 文件图标组件，使用SVG实现类似Material Icons的效果
interface FileIconProps {
  extension: string
  fileName?: string
  isDirectory?: boolean
  size?: number
  className?: string
}

// 主要的文件图标组件
export const FileIcon: React.FC<FileIconProps> = ({
  extension,
  fileName = '',
  isDirectory = false,
  size = DEFAULT_ICON_SIZE,
  className = '',
}) => {
  if (isDirectory) {
    return <FolderIcon size={size} className={className} />
  }

  const ext = extension.toLowerCase()
  const fullName = fileName.toLowerCase()

  // 根据文件扩展名返回对应图标
  switch (ext) {
    case 'py':
    case 'pyw':
    case 'pyc':
      return <PythonIcon size={size} className={className} />

    case 'js':
    case 'jsx':
    case 'mjs':
      return <JavaScriptIcon size={size} className={className} />

    case 'ts':
    case 'tsx':
      return <TypeScriptIcon size={size} className={className} />

    case 'java':
      return <JavaIcon size={size} className={className} />

    case 'go':
      return <GoIcon size={size} className={className} />

    case 'json':
    case 'jsonc':
      return <JsonIcon size={size} className={className} />

    case 'md':
    case 'markdown':
    case 'mdown':
      return <MarkdownIcon size={size} className={className} />

    case 'css':
    case 'scss':
    case 'sass':
    case 'less':
      return <CssIcon size={size} className={className} />

    case 'html':
    case 'htm':
    case 'xhtml':
      return <HtmlIcon size={size} className={className} />

    case 'yml':
    case 'yaml':
    case 'xml':
    case 'toml':
    case 'ini':
    case 'cfg':
    case 'conf':
      return <ConfigIcon size={size} className={className} />

    case 'png':
    case 'jpg':
    case 'jpeg':
    case 'gif':
    case 'webp':
    case 'svg':
    case 'ico':
    case 'bmp':
      return <ImageIcon size={size} className={className} />

    default:
      // 特殊文件名处理
      if (fullName.includes('dockerfile') || fullName === 'dockerfile') {
        return <ConfigIcon size={size} className={className} />
      }
      if (fullName.includes('readme') || fullName.includes('license')) {
        return <MarkdownIcon size={size} className={className} />
      }

      return <DefaultFileIcon size={size} className={className} />
  }
}

// 获取文件类型颜色（用于文本显示等）
export const getFileTypeColor = (extension: string): string => {
  const ext = extension.toLowerCase()

  const colorMap: Record<string, string> = {
    py: '#3776ab',
    js: '#f7df1e',
    jsx: '#f7df1e',
    ts: '#3178c6',
    tsx: '#3178c6',
    java: '#ed8b00',
    go: '#00add8',
    json: '#ffa726',
    md: '#2196f3',
    css: '#1976d2',
    scss: '#c6538c',
    sass: '#c6538c',
    html: '#e65100',
    xml: '#757575',
    yml: '#757575',
    yaml: '#757575',
    png: '#4caf50',
    jpg: '#4caf50',
    jpeg: '#4caf50',
    gif: '#4caf50',
    svg: '#4caf50',
  }

  return colorMap[ext] || '#90a4ae'
}

export default FileIcon
