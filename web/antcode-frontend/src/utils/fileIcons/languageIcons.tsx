import { DEFAULT_ICON_SIZE, type IconGlyph } from './iconBase'

// antcode_contracts.execution_language 承诺的五种执行语言的图标。这一组是封闭集合：
// 增减语言必须与后端 ExecutionLanguage 枚举同步，不能只在前端单方面加。

// Python文件图标
export const PythonIcon: IconGlyph = ({ size = DEFAULT_ICON_SIZE, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#3776ab"
      fillOpacity="0.1"
    />
    <path d="M14 2V8H20" stroke="#3776ab" strokeWidth="1" fill="none" />
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
export const JavaScriptIcon: IconGlyph = ({ size = DEFAULT_ICON_SIZE, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#f7df1e"
      fillOpacity="0.2"
    />
    <path d="M14 2V8H20" stroke="#f7df1e" strokeWidth="1" fill="none" />
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
export const TypeScriptIcon: IconGlyph = ({ size = DEFAULT_ICON_SIZE, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#3178c6"
      fillOpacity="0.2"
    />
    <path d="M14 2V8H20" stroke="#3178c6" strokeWidth="1" fill="none" />
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

// Java文件图标
export const JavaIcon: IconGlyph = ({ size = DEFAULT_ICON_SIZE, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#ed8b00"
      fillOpacity="0.2"
    />
    <path d="M14 2V8H20" stroke="#ed8b00" strokeWidth="1" fill="none" />
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
export const GoIcon: IconGlyph = ({ size = DEFAULT_ICON_SIZE, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#00add8"
      fillOpacity="0.2"
    />
    <path d="M14 2V8H20" stroke="#00add8" strokeWidth="1" fill="none" />
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
