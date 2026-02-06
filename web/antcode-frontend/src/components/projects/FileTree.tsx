import React, { useState, useCallback } from 'react'
import {
  Tree,
  Button,
  Space,
  Typography,
  Tooltip,
  Spin,
  Empty,
  Alert,
  theme
} from 'antd'
import {
  FolderOutlined,
  FolderOpenOutlined,
  EyeOutlined,
  DownloadOutlined
} from '@ant-design/icons'
import type { TreeDataNode, TreeProps } from 'antd'
import { FileIcon } from '@/utils/fileIcons'
import styles from './FileTree.module.css'
import './FileTree.global.css'

const { Text } = Typography

// 格式化文件大小
const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

// 文件节点接口
interface FileNode {
  name: string
  type: 'file' | 'directory'
  path: string
  size?: number
  modified_time?: number
  mime_type?: string
  is_text?: boolean
  children?: FileNode[]
  children_count?: number
}

type FileTreeDataNode = TreeDataNode & { data: FileNode }

interface FileTreeProps {
  projectId: number
  fileStructure?: {
    structure: FileNode
    total_files: number
    total_size: number
  }
  loading?: boolean
  onPreviewFile?: (filePath: string, fileName: string) => void
  onDownloadFile?: (filePath: string, fileName: string) => void
  className?: string
}

const FileTree: React.FC<FileTreeProps> = ({
  projectId: _projectId,
  fileStructure,
  loading = false,
  onPreviewFile,
  onDownloadFile,
  className
}) => {
  const { token } = theme.useToken()
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([])

  // 转换文件结构为Tree组件需要的格式
  const convertToTreeData = useCallback((node: FileNode, parentPath = ''): FileTreeDataNode => {
    const currentPath = parentPath ? `${parentPath}/${node.name}` : node.name
    const isDirectory = node.type === 'directory'
    
    const treeNode: FileTreeDataNode = {
      key: currentPath,
      // 保存原始节点数据供后续使用
      data: node,
      title: (
        <div className={styles['file-tree-node']}>
          <Space size="small">
            <span 
              className={styles['file-icon']}
              style={{ cursor: 'pointer' }}
              onClick={(e) => {
                e.stopPropagation()
                if (isDirectory) {
                  // 文件夹图标：切换展开状态
                  const isExpanded = expandedKeys.includes(currentPath)
                  if (isExpanded) {
                    setExpandedKeys(expandedKeys.filter(key => key !== currentPath))
                  } else {
                    setExpandedKeys([...expandedKeys, currentPath])
                  }
                } else {
                  // 文件图标：预览文件（包括非文本文件）
                  onPreviewFile?.(node.path, node.name)
                }
              }}
            >
              {isDirectory ? (
                expandedKeys.includes(currentPath) ? (
                  <FolderOpenOutlined style={{ color: token.colorInfo }} />
                ) : (
                  <FolderOutlined style={{ color: token.colorInfo }} />
                )
              ) : (
                <FileIcon 
                  extension={node.name.split('.').pop() || ''}
                  fileName={node.name}
                  size={16}
                />
              )}
            </span>
            <Text 
              className={styles['file-name']} 
              title={node.name}
              style={{ 
                cursor: 'pointer', // 所有文件和文件夹都可以点击
                flex: 1
              }}
              onClick={(e) => {
                e.stopPropagation()
                if (isDirectory) {
                  // 文件夹：切换展开状态
                  const isExpanded = expandedKeys.includes(currentPath)
                  if (isExpanded) {
                    setExpandedKeys(expandedKeys.filter(key => key !== currentPath))
                  } else {
                    setExpandedKeys([...expandedKeys, currentPath])
                  }
                } else {
                  // 文件：预览文件（包括非文本文件）
                  onPreviewFile?.(node.path, node.name)
                }
              }}
            >
              {node.name}
            </Text>
            {node.size !== undefined && (
              <Text type="secondary" className={styles['file-size']}>
                {formatFileSize(node.size)}
              </Text>
            )}
            {!isDirectory && (
              <Space size="small" className={styles['file-actions']}>
                {node.is_text && (
                  <Tooltip title="预览文件">
                    <Button
                      type="text"
                      size="small"
                      icon={<EyeOutlined />}
                      onClick={(e) => {
                        e.stopPropagation()
                        onPreviewFile?.(node.path, node.name)
                      }}
                    />
                  </Tooltip>
                )}
                <Tooltip title="下载文件">
                  <Button
                    type="text"
                    size="small"
                    icon={<DownloadOutlined />}
                    onClick={(e) => {
                      e.stopPropagation()
                      onDownloadFile?.(node.path, node.name)
                    }}
                  />
                </Tooltip>
              </Space>
            )}
          </Space>
        </div>
      ),
      isLeaf: !isDirectory,
      children: isDirectory && node.children 
        ? node.children.map(child => convertToTreeData(child, currentPath))
        : undefined
    }

    return treeNode
  }, [expandedKeys, onPreviewFile, onDownloadFile, token.colorInfo])

  const handleExpand = (expandedKeysValue: React.Key[]) => {
    setExpandedKeys(expandedKeysValue)
  }

  // 处理节点点击事件
  const handleNodeClick: TreeProps<FileTreeDataNode>['onSelect'] = (_selectedKeys, info) => {
    const { node } = info
    const nodeKey = node.key
    
    // 如果点击的是文件夹，切换展开状态
    if (!node.isLeaf) {
      const isExpanded = expandedKeys.includes(nodeKey)
      if (isExpanded) {
        // 收起文件夹
        setExpandedKeys(expandedKeys.filter(key => key !== nodeKey))
      } else {
        // 展开文件夹
        setExpandedKeys([...expandedKeys, nodeKey])
      }
    } else {
      // 如果点击的是文件，预览文件
      const nodeData = node.data
      if (nodeData && nodeData.path && nodeData.name) {
        onPreviewFile?.(nodeData.path, nodeData.name)
      }
    }
  }

  if (loading) {
    return (
      <div className={`${styles['file-tree-container']} ${className || ''}`}>
        <Spin tip="正在加载文件结构...">
          <div style={{ height: 200 }} />
        </Spin>
      </div>
    )
  }

  if (!fileStructure || !fileStructure.structure) {
    return (
      <div className={`${styles['file-tree-container']} ${className || ''}`}>
        <Empty 
          description="没有找到文件结构" 
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      </div>
    )
  }

  const treeData: FileTreeDataNode[] = [convertToTreeData(fileStructure.structure)]

  return (
    <div className={`${styles['file-tree-container']} ${className || ''}`}>
      {/* 文件统计信息 */}
      <div className={styles['file-stats']}>
        <Alert
          message={
            <Space>
              <Text>
                <strong>总计:</strong> {fileStructure.total_files} 个文件
              </Text>
              <Text>
                <strong>大小:</strong> {formatFileSize(fileStructure.total_size)}
              </Text>
            </Space>
          }
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
      </div>

      {/* 文件树 */}
      <div className={styles['file-tree']}>
        <div style={{ minWidth: 'max-content' }}>
          <Tree
            treeData={treeData}
            expandedKeys={expandedKeys}
            onExpand={handleExpand}
            onSelect={handleNodeClick}
            showLine={{ showLeafIcon: false }}
            blockNode={false}
            defaultExpandAll={false}
            switcherIcon={({ expanded, isLeaf }) => {
              if (isLeaf) {
                return null
              }
              return expanded ? (
                <FolderOpenOutlined style={{ color: token.colorInfo }} />
              ) : (
                <FolderOutlined style={{ color: token.colorInfo }} />
              )
            }}
          />
        </div>
      </div>
    </div>
  )
}

export default React.memo(FileTree)
