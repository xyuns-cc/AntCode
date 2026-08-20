import { DEFAULT_ICON_SIZE, type IconGlyph } from './iconBase'

// 非执行类文件的图标：数据、标记、样式、配置与图片。这一组是开放集合，
// 可以随仓库里出现的新文件类型自由增补，不受执行语言契约约束。

// JSON文件图标
export const JsonIcon: IconGlyph = ({ size = DEFAULT_ICON_SIZE, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#ffa726"
      fillOpacity="0.2"
    />
    <path d="M14 2V8H20" stroke="#ffa726" strokeWidth="1" fill="none" />
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
export const MarkdownIcon: IconGlyph = ({ size = DEFAULT_ICON_SIZE, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#2196f3"
      fillOpacity="0.2"
    />
    <path d="M14 2V8H20" stroke="#2196f3" strokeWidth="1" fill="none" />
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
export const CssIcon: IconGlyph = ({ size = DEFAULT_ICON_SIZE, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#1976d2"
      fillOpacity="0.2"
    />
    <path d="M14 2V8H20" stroke="#1976d2" strokeWidth="1" fill="none" />
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
export const HtmlIcon: IconGlyph = ({ size = DEFAULT_ICON_SIZE, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#e65100"
      fillOpacity="0.2"
    />
    <path d="M14 2V8H20" stroke="#e65100" strokeWidth="1" fill="none" />
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

// 配置文件图标 (YAML, XML, etc.)
export const ConfigIcon: IconGlyph = ({ size = DEFAULT_ICON_SIZE, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#757575"
      fillOpacity="0.2"
    />
    <path d="M14 2V8H20" stroke="#757575" strokeWidth="1" fill="none" />
    <circle cx="9" cy="12" r="1" fill="#757575" />
    <circle cx="12" cy="12" r="1" fill="#757575" />
    <circle cx="15" cy="12" r="1" fill="#757575" />
    <circle cx="9" cy="15" r="1" fill="#757575" />
    <circle cx="12" cy="15" r="1" fill="#757575" />
    <circle cx="15" cy="15" r="1" fill="#757575" />
  </svg>
)

// 图片文件图标
export const ImageIcon: IconGlyph = ({ size = DEFAULT_ICON_SIZE, className = '' }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className={className}>
    <path
      d="M14 2H6C4.89 2 4 2.9 4 4V20C4 21.1 4.89 22 6 22H18C19.1 22 20 21.1 20 20V8L14 2Z"
      fill="#4caf50"
      fillOpacity="0.2"
    />
    <path d="M14 2V8H20" stroke="#4caf50" strokeWidth="1" fill="none" />
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
