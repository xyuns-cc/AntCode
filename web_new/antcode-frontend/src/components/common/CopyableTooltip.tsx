import React from 'react'
import { Tooltip, Button, message } from 'antd'
import { CopyOutlined } from '@ant-design/icons'
import type { TooltipProps } from 'antd'

interface CopyableTooltipProps extends Omit<TooltipProps, 'title'> {
  text: string
  children: React.ReactElement
}

/**
 * 带复制按钮的 Tooltip 组件
 * 在气泡中显示完整文本，并在气泡内提供蓝色复制按钮
 */
const CopyableTooltip: React.FC<CopyableTooltipProps> = React.memo(({ 
  text, 
  children, 
  placement = 'topLeft',
  ...restProps 
}) => {
  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
    try {
      await navigator.clipboard.writeText(text)
      message.success('已复制到剪贴板')
    } catch (_error) {
      // 降级方案：使用传统方法
      const textArea = document.createElement('textarea')
      textArea.value = text
      textArea.style.position = 'fixed'
      textArea.style.opacity = '0'
      document.body.appendChild(textArea)
      textArea.select()
      try {
        document.execCommand('copy')
        message.success('已复制到剪贴板')
      } catch (_err) {
        message.error('复制失败')
      }
      document.body.removeChild(textArea)
    }
  }

  const title = (
    <>
      {text}
      <Button
        type="text"
        size="small"
        icon={<CopyOutlined />}
        onClick={handleCopy}
        style={{ 
          color: '#1677ff',
          padding: '0 4px',
          height: '20px',
          minWidth: '20px',
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          marginLeft: '8px',
          verticalAlign: 'middle',
          backgroundColor: 'transparent',
          border: 'none'
        }}
      />
    </>
  )

  return (
    <Tooltip 
      title={title} 
      placement={placement}
      overlayStyle={{ maxWidth: '500px' }}
      {...restProps}
    >
      {children}
    </Tooltip>
  )
})

CopyableTooltip.displayName = 'CopyableTooltip'

export default CopyableTooltip
