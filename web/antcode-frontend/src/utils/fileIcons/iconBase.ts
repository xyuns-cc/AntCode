import type React from 'react'

// 16px 与 antd Space 内相邻正文的行高对齐，图标与文字基线不会错位。
export const DEFAULT_ICON_SIZE = 16

export interface IconGlyphProps {
  size?: number
  className?: string
}

export type IconGlyph = React.FC<IconGlyphProps>
