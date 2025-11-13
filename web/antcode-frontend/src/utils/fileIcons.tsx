import React from 'react'

// 文件图标组件，使用SVG实现类似Material Icons的效果
interface FileIconProps {
  extension: string
  fileName?: string
  isDirectory?: boolean
  size?: number
  className?: string
}

// 目录图标
const FolderIcon: React.FC<{ size?: number; className?: string }> = ({ 
  size = 16, 
  className = '' 
}) => (
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
const DefaultFileIcon: React.FC<{ size?: number; className?: string; color?: string }> = ({ 
  size = 16, 
  className = '',
  color = '#90a4ae' 
}) => (
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
    <path
      d="M14 2V8H20"
      stroke="currentColor"
      strokeWidth="1.5"
      fill="none"
    />
  </svg>
)

// Python文件图标
const PythonIcon: React.FC<{ size?: number; className?: string }> = ({ 
  size = 16, 
  className = '' 
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={className}
  >
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#3776ab"
      fillOpacity="0.1"
    />
    <path
      d="M14 2V8H20"
      stroke="#3776ab"
      strokeWidth="1"
      fill="none"
    />
    <path
      d="M8 10C8 9.45 8.45 9 9 9H11C11.55 9 12 9.45 12 10V12C12 12.55 11.55 13 11 13H9C8.45 13 8 12.55 8 12V10Z"
      fill="#3776ab"
    />
    <path
      d="M12 14C12 13.45 12.45 13 13 13H15C15.55 13 16 13.45 16 14V16C16 16.55 15.55 17 15 17H13C12.45 17 12 16.55 12 16V14Z"
      fill="#ffd43b"
    />
  </svg>
)

// JavaScript文件图标
const JavaScriptIcon: React.FC<{ size?: number; className?: string }> = ({ 
  size = 16, 
  className = '' 
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={className}
  >
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#f7df1e"
      fillOpacity="0.2"
    />
    <path
      d="M14 2V8H20"
      stroke="#f7df1e"
      strokeWidth="1"
      fill="none"
    />
    <text
      x="12"
      y="16"
      textAnchor="middle"
      fill="#f7df1e"
      fontSize="8"
      fontWeight="bold"
      fontFamily="monospace"
    >
      JS
    </text>
  </svg>
)

// TypeScript文件图标
const TypeScriptIcon: React.FC<{ size?: number; className?: string }> = ({ 
  size = 16, 
  className = '' 
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={className}
  >
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#3178c6"
      fillOpacity="0.2"
    />
    <path
      d="M14 2V8H20"
      stroke="#3178c6"
      strokeWidth="1"
      fill="none"
    />
    <text
      x="12"
      y="16"
      textAnchor="middle"
      fill="#3178c6"
      fontSize="7"
      fontWeight="bold"
      fontFamily="monospace"
    >
      TS
    </text>
  </svg>
)

// JSON文件图标
const JsonIcon: React.FC<{ size?: number; className?: string }> = ({ 
  size = 16, 
  className = '' 
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={className}
  >
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#ffa726"
      fillOpacity="0.2"
    />
    <path
      d="M14 2V8H20"
      stroke="#ffa726"
      strokeWidth="1"
      fill="none"
    />
    <path
      d="M8 12C8 11.45 8.45 11 9 11H9.5C9.78 11 10 11.22 10 11.5S9.78 12 9.5 12H9V13H9.5C9.78 13 10 13.22 10 13.5S9.78 14 9.5 14H9C8.45 14 8 13.55 8 13V12Z"
      fill="#ffa726"
    />
    <path
      d="M14 11V14C14 14.55 14.45 15 15 15H15.5C15.78 15 16 14.78 16 14.5S15.78 14 15.5 14H15V11H15.5C15.78 11 16 10.78 16 10.5S15.78 10 15.5 10H15C14.45 10 14 10.45 14 11Z"
      fill="#ffa726"
    />
  </svg>
)

// Markdown文件图标
const MarkdownIcon: React.FC<{ size?: number; className?: string }> = ({ 
  size = 16, 
  className = '' 
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={className}
  >
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#2196f3"
      fillOpacity="0.2"
    />
    <path
      d="M14 2V8H20"
      stroke="#2196f3"
      strokeWidth="1"
      fill="none"
    />
    <text
      x="12"
      y="16"
      textAnchor="middle"
      fill="#2196f3"
      fontSize="6"
      fontWeight="bold"
      fontFamily="monospace"
    >
      MD
    </text>
  </svg>
)

// CSS文件图标
const CssIcon: React.FC<{ size?: number; className?: string }> = ({ 
  size = 16, 
  className = '' 
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={className}
  >
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#1976d2"
      fillOpacity="0.2"
    />
    <path
      d="M14 2V8H20"
      stroke="#1976d2"
      strokeWidth="1"
      fill="none"
    />
    <text
      x="12"
      y="16"
      textAnchor="middle"
      fill="#1976d2"
      fontSize="7"
      fontWeight="bold"
      fontFamily="monospace"
    >
      CSS
    </text>
  </svg>
)

// HTML文件图标
const HtmlIcon: React.FC<{ size?: number; className?: string }> = ({ 
  size = 16, 
  className = '' 
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={className}
  >
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#e65100"
      fillOpacity="0.2"
    />
    <path
      d="M14 2V8H20"
      stroke="#e65100"
      strokeWidth="1"
      fill="none"
    />
    <text
      x="12"
      y="16"
      textAnchor="middle"
      fill="#e65100"
      fontSize="6"
      fontWeight="bold"
      fontFamily="monospace"
    >
      HTML
    </text>
  </svg>
)

// Java文件图标
const JavaIcon: React.FC<{ size?: number; className?: string }> = ({ 
  size = 16, 
  className = '' 
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={className}
  >
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#ed8b00"
      fillOpacity="0.2"
    />
    <path
      d="M14 2V8H20"
      stroke="#ed8b00"
      strokeWidth="1"
      fill="none"
    />
    <text
      x="12"
      y="16"
      textAnchor="middle"
      fill="#ed8b00"
      fontSize="6"
      fontWeight="bold"
      fontFamily="monospace"
    >
      JAVA
    </text>
  </svg>
)

// Go文件图标
const GoIcon: React.FC<{ size?: number; className?: string }> = ({ 
  size = 16, 
  className = '' 
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={className}
  >
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#00add8"
      fillOpacity="0.2"
    />
    <path
      d="M14 2V8H20"
      stroke="#00add8"
      strokeWidth="1"
      fill="none"
    />
    <text
      x="12"
      y="16"
      textAnchor="middle"
      fill="#00add8"
      fontSize="7"
      fontWeight="bold"
      fontFamily="monospace"
    >
      GO
    </text>
  </svg>
)

// 配置文件图标 (YAML, XML, etc.)
const ConfigIcon: React.FC<{ size?: number; className?: string }> = ({ 
  size = 16, 
  className = '' 
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={className}
  >
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#757575"
      fillOpacity="0.2"
    />
    <path
      d="M14 2V8H20"
      stroke="#757575"
      strokeWidth="1"
      fill="none"
    />
    <circle cx="9" cy="12" r="1" fill="#757575" />
    <circle cx="12" cy="12" r="1" fill="#757575" />
    <circle cx="15" cy="12" r="1" fill="#757575" />
    <circle cx="9" cy="15" r="1" fill="#757575" />
    <circle cx="12" cy="15" r="1" fill="#757575" />
    <circle cx="15" cy="15" r="1" fill="#757575" />
  </svg>
)

// 图片文件图标
const ImageIcon: React.FC<{ size?: number; className?: string }> = ({ 
  size = 16, 
  className = '' 
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    className={className}
  >
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#4caf50"
      fillOpacity="0.2"
    />
    <path
      d="M14 2V8H20"
      stroke="#4caf50"
      strokeWidth="1"
      fill="none"
    />
    <circle cx="10" cy="11" r="1.5" fill="#4caf50" />
    <path
      d="M8 16L10 14L12 16L14 14L16 16"
      stroke="#4caf50"
      strokeWidth="1.5"
      fill="none"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
)

// 主要的文件图标组件
export const FileIcon: React.FC<FileIconProps> = ({ 
  extension, 
  fileName = '', 
  isDirectory = false, 
  size = 16, 
  className = '' 
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
    'py': '#3776ab',
    'js': '#f7df1e',
    'jsx': '#f7df1e',
    'ts': '#3178c6',
    'tsx': '#3178c6',
    'java': '#ed8b00',
    'go': '#00add8',
    'json': '#ffa726',
    'md': '#2196f3',
    'css': '#1976d2',
    'scss': '#c6538c',
    'sass': '#c6538c',
    'html': '#e65100',
    'xml': '#757575',
    'yml': '#757575',
    'yaml': '#757575',
    'png': '#4caf50',
    'jpg': '#4caf50',
    'jpeg': '#4caf50',
    'gif': '#4caf50',
    'svg': '#4caf50'
  }
  
  return colorMap[ext] || '#90a4ae'
}

export default FileIcon
