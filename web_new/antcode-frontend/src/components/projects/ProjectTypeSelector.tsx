import React, { useMemo } from 'react'
import { Row, Col, Typography, theme } from 'antd'
import {
  FileOutlined,
  SettingOutlined,
  CodeOutlined,
  CheckCircleFilled
} from '@ant-design/icons'
import { useThemeContext } from '@/contexts/ThemeContext'
import type { ProjectType } from '@/types'

const { Title, Text } = Typography

interface ProjectTypeSelectorProps {
  selectedType: ProjectType | null
  onSelect: (type: ProjectType) => void
}

const ProjectTypeSelector: React.FC<ProjectTypeSelectorProps> = ({
  selectedType,
  onSelect
}) => {
  const { isDark } = useThemeContext()
  const { token } = theme.useToken()
  
  const projectTypes = useMemo(() => [
    {
      type: 'file' as ProjectType,
      title: '文件项目',
      description: '上传项目文件或压缩包',
      icon: FileOutlined,
      color: token.colorInfo,
      features: ['支持 .zip、.tar.gz 压缩包', '自动解析项目依赖']
    },
    {
      type: 'rule' as ProjectType,
      title: '规则项目',
      description: '配置网页采集规则',
      icon: SettingOutlined,
      color: token.colorSuccess,
      features: ['可视化规则配置', '支持列表页和详情页']
    },
    {
      type: 'code' as ProjectType,
      title: '代码项目',
      description: '编写自定义代码',
      icon: CodeOutlined,
      color: token.purple || '#722ed1',
      features: ['在线代码编辑器', '快速部署执行']
    }
  ], [token])

  return (
    <div style={{ padding: '8px 0' }}>
      <div style={{ textAlign: 'center', marginBottom: 20 }}>
        <Title level={4} style={{ marginBottom: 4 }}>选择项目类型</Title>
        <Text type="secondary" style={{ fontSize: 13 }}>
          选择适合您需求的项目类型
        </Text>
      </div>

      <Row gutter={[16, 16]}>
        {projectTypes.map((item) => {
          const isSelected = selectedType === item.type
          const IconComponent = item.icon
          
          return (
            <Col xs={24} sm={12} key={item.type}>
              <div
                onClick={() => onSelect(item.type)}
                style={{
                  position: 'relative',
                  padding: '16px',
                  borderRadius: 12,
                  border: `2px solid ${isSelected ? item.color : token.colorBorderSecondary}`,
                  background: isSelected 
                    ? (isDark ? `${item.color}15` : `${item.color}08`)
                    : token.colorBgContainer,
                  cursor: 'pointer',
                  transition: 'all 0.2s ease',
                  minHeight: 100,
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 14
                }}
                onMouseEnter={(e) => {
                  if (!isSelected) {
                    e.currentTarget.style.borderColor = item.color
                    e.currentTarget.style.transform = 'translateY(-2px)'
                    e.currentTarget.style.boxShadow = `0 4px 12px ${isDark ? 'rgba(0,0,0,0.3)' : 'rgba(0,0,0,0.1)'}`
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isSelected) {
                    e.currentTarget.style.borderColor = token.colorBorderSecondary
                    e.currentTarget.style.transform = 'translateY(0)'
                    e.currentTarget.style.boxShadow = 'none'
                  }
                }}
              >
                {/* 选中标记 */}
                {isSelected && (
                  <CheckCircleFilled 
                    style={{ 
                      position: 'absolute',
                      top: 10,
                      right: 10,
                      fontSize: 16,
                      color: item.color
                    }} 
                  />
                )}
                
                {/* 左侧：图标和标题 */}
                <div style={{ 
                  display: 'flex', 
                  flexDirection: 'column', 
                  alignItems: 'center',
                  flexShrink: 0,
                  width: 70
                }}>
                  <div style={{
                    width: 44,
                    height: 44,
                    borderRadius: 10,
                    backgroundColor: isSelected 
                      ? `${item.color}20` 
                      : token.colorFillSecondary,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    marginBottom: 8
                  }}>
                    <IconComponent style={{ fontSize: 22, color: item.color }} />
                  </div>
                  <Text 
                    strong 
                    style={{ 
                      fontSize: 13,
                      color: isSelected ? item.color : token.colorText,
                      textAlign: 'center'
                    }}
                  >
                    {item.title}
                  </Text>
                </div>
                
                {/* 右侧：描述和特性 */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Text 
                    type="secondary" 
                    style={{ fontSize: 12, display: 'block', marginBottom: 6 }}
                  >
                    {item.description}
                  </Text>
                  
                  <div>
                    {item.features.map((feature, index) => (
                      <div 
                        key={index} 
                        style={{ 
                          fontSize: 11, 
                          color: token.colorTextTertiary,
                          lineHeight: 1.5
                        }}
                      >
                        • {feature}
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </Col>
          )
        })}
      </Row>
    </div>
  )
}

export default ProjectTypeSelector
