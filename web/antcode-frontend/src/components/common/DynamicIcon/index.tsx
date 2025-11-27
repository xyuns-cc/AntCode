import React from 'react'
import * as Icons from '@ant-design/icons'

interface DynamicIconProps {
  /** Ant Design 图标名称，如 'RocketOutlined' */
  name: string
  /** 自定义样式 */
  style?: React.CSSProperties
  /** 自定义类名 */
  className?: string
}

/**
 * 动态图标组件，根据名称渲染 Ant Design 图标
 */
const DynamicIcon: React.FC<DynamicIconProps> = ({ name, style, className }) => {
  const IconComponent = (Icons as Record<string, React.ComponentType<{ style?: React.CSSProperties; className?: string }>>)[name]

  if (!IconComponent) {
    // 如果找不到图标，返回默认图标
    const DefaultIcon = Icons.AppstoreOutlined
    return <DefaultIcon style={style} className={className} />
  }

  return <IconComponent style={style} className={className} />
}

export default DynamicIcon

