import React from 'react'
import { Card, Row, Col, Typography, Space } from 'antd'
import {
  FileOutlined,
  SettingOutlined,
  CodeOutlined,
  UploadOutlined,
  GlobalOutlined,
  EditOutlined
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
  const projectTypes = [
    {
      type: 'file' as ProjectType,
      title: '文件项目',
      description: '上传完整的项目文件或压缩包',
      icon: <FileOutlined style={{ fontSize: 32, color: '#1890ff' }} />,
      features: [
        '支持 .zip、.tar.gz 压缩包',
        '支持单个 Python 文件',
        '自动解析项目依赖',
        '完整的项目结构管理'
      ],
      detailIcon: <UploadOutlined />,
      color: '#1890ff'
    },
    {
      type: 'rule' as ProjectType,
      title: '规则项目',
      description: '配置网页数据采集规则',
      icon: <SettingOutlined style={{ fontSize: 32, color: '#52c41a' }} />,
      features: [
        '可视化规则配置',
        '支持列表页和详情页',
        '多种采集引擎',
        '灵活的翻页策略'
      ],
      detailIcon: <GlobalOutlined />,
      color: '#52c41a'
    },
    {
      type: 'code' as ProjectType,
      title: '代码项目',
      description: '直接编写或上传源代码',
      icon: <CodeOutlined style={{ fontSize: 32, color: '#722ed1' }} />,
      features: [
        '在线代码编辑器',
        '支持多种编程语言',
        '实时语法检查',
        '快速部署执行'
      ],
      detailIcon: <EditOutlined />,
      color: '#722ed1'
    }
  ]

  return (
    <div>
      <div style={{ textAlign: 'center', marginBottom: 32 }}>
        <Title level={3}>选择项目类型</Title>
        <Text type="secondary">
          根据您的需求选择合适的项目类型，不同类型有不同的配置方式
        </Text>
      </div>

      <Row gutter={[16, 16]}>
        {projectTypes.map((item) => (
          <Col span={24} key={item.type}>
            <Card
              hoverable
              className={`project-type-card ${selectedType === item.type ? 'selected' : ''}`}
              onClick={() => onSelect(item.type)}
              style={{
                border: selectedType === item.type ? `2px solid ${item.color}` : `1px solid ${isDark ? '#424242' : '#d9d9d9'}`,
                borderRadius: 8,
                transition: 'all 0.3s ease',
                backgroundColor: isDark ? '#1f1f1f' : '#ffffff'
              }}
              styles={{ body: { padding: 20 } }}
            >
              <Row align="middle" gutter={16}>
                <Col flex="none">
                  <div style={{
                    width: 64,
                    height: 64,
                    borderRadius: 8,
                    backgroundColor: selectedType === item.type 
                      ? `${item.color}15` 
                      : isDark ? '#2a2a2a' : '#f5f5f5',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    transition: 'all 0.3s ease'
                  }}>
                    {item.icon}
                  </div>
                </Col>
                
                <Col flex="auto">
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <Title level={4} style={{ margin: 0, color: item.color }}>
                        {item.title}
                      </Title>
                      {item.detailIcon}
                    </div>
                    
                    <Text type="secondary" style={{ fontSize: 14 }}>
                      {item.description}
                    </Text>
                    
                    <div style={{ marginTop: 8 }}>
                      {item.features.map((feature, index) => (
                        <div key={index} style={{ 
                          fontSize: 12, 
                          color: isDark ? 'rgba(255, 255, 255, 0.65)' : '#666',
                          marginBottom: 2
                        }}>
                          • {feature}
                        </div>
                      ))}
                    </div>
                  </Space>
                </Col>
                
                <Col flex="none">
                  {selectedType === item.type && (
                    <div style={{
                      width: 24,
                      height: 24,
                      borderRadius: '50%',
                      backgroundColor: item.color,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      color: 'white',
                      fontSize: 12,
                      fontWeight: 'bold'
                    }}>
                      ✓
                    </div>
                  )}
                </Col>
              </Row>
            </Card>
          </Col>
        ))}
      </Row>

      <style>{`
        .project-type-card {
          cursor: pointer;
        }
        
        .project-type-card:hover {
          box-shadow: 0 4px 12px ${isDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.1)'};
          transform: translateY(-2px);
        }
        
        .project-type-card.selected {
          box-shadow: 0 4px 16px rgba(24, 144, 255, 0.2);
        }
      `}</style>
    </div>
  )
}

export default ProjectTypeSelector
