import React, { useState, useEffect, useCallback } from 'react'
import {
  Button,
  Space,
  Typography,
  Alert,
  Spin,
  Empty,
  Tooltip,
  Tag,
  
} from 'antd'
import showNotification from '@/utils/notification'
import {
  DownloadOutlined,
  CopyOutlined,
  FullscreenOutlined,
  FullscreenExitOutlined,
  FileTextOutlined,
  EditOutlined,
  SaveOutlined,
  CloseOutlined
} from '@ant-design/icons'
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter'
import { tomorrow, prism } from 'react-syntax-highlighter/dist/esm/styles/prism'
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash'
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json'
import yaml from 'react-syntax-highlighter/dist/esm/languages/prism/yaml'
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python'
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript'
import typescript from 'react-syntax-highlighter/dist/esm/languages/prism/typescript'
import markdown from 'react-syntax-highlighter/dist/esm/languages/prism/markdown'
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql'
import jsx from 'react-syntax-highlighter/dist/esm/languages/prism/jsx'
import tsx from 'react-syntax-highlighter/dist/esm/languages/prism/tsx'
import markup from 'react-syntax-highlighter/dist/esm/languages/prism/markup'
import css from 'react-syntax-highlighter/dist/esm/languages/prism/css'

const PRE_REGISTERED_LANGUAGES: Record<string, any> = {
  bash,
  sh: bash,
  shell: bash,
  json,
  yaml,
  yml: yaml,
  python,
  py: python,
  javascript,
  js: javascript,
  typescript,
  ts: typescript,
  markdown,
  md: markdown,
  sql,
  jsx,
  tsx,
  html: markup,
  css,
}

Object.entries(PRE_REGISTERED_LANGUAGES).forEach(([name, language]) => {
  SyntaxHighlighter.registerLanguage(name, language)
})
import { useThemeContext } from '@/contexts/ThemeContext'
// 懒加载 CodeEditor，避免非编辑场景拉取 monaco-editor
const LazyCodeEditor = React.lazy(() => import('@/components/ui/CodeEditor'))
import type { ProjectFileContent } from '@/types'
import styles from './FileViewer.module.css'

const { Text } = Typography

// 语言映射
const FALLBACK_LANGUAGE = 'text'

const getLanguageFromMimeType = (mimeType: string, fileName: string): string => {
  const extension = (fileName.split('.').pop() || '').toLowerCase()

  if (extension && PRE_REGISTERED_LANGUAGES[extension]) {
    return extension
  }

  if (mimeType) {
    if (mimeType.includes('python')) return 'python'
    if (mimeType.includes('javascript')) return 'javascript'
    if (mimeType.includes('json')) return 'json'
    if (mimeType.includes('yaml')) return 'yaml'
    if (mimeType.includes('bash') || mimeType.includes('shell')) return 'bash'
    if (mimeType.includes('markdown')) return 'markdown'
    if (mimeType.includes('sql')) return 'sql'
  }

  return FALLBACK_LANGUAGE
}

// 格式化文件大小
const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

// 格式化时间
const formatTime = (timestamp: number): string => {
  return new Date(timestamp * 1000).toLocaleString('zh-CN')
}

interface FileViewerProps {
  filePath?: string
  fileName?: string
  fileData?: ProjectFileContent
  loading?: boolean
  error?: string | null
  onClose?: () => void
  onDownload?: (filePath: string, fileName: string) => void
  onSave?: (filePath: string, content: string) => Promise<ProjectFileContent>
  saving?: boolean
  className?: string
}

const FileViewer: React.FC<FileViewerProps> = ({
  filePath,
  fileName,
  fileData,
  loading = false,
  error = null,
  onClose,
  onDownload,
  onSave,
  saving = false,
  className
}) => {
  const { isDark } = useThemeContext()
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [copied, setCopied] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  const [draftContent, setDraftContent] = useState('')

  const canEdit = Boolean(
    fileData?.is_text &&
    !fileData?.too_large &&
    !fileData?.binary &&
    typeof fileData?.content === 'string'
  )
  const language = fileName ? getLanguageFromMimeType(fileData?.mime_type || '', fileName) : 'text'
  const editorHeight = isFullscreen ? 'calc(100vh - 180px)' : 500

  useEffect(() => {
    if (!isEditing) {
      setDraftContent(typeof fileData?.content === 'string' ? fileData.content : '')
    }
  }, [fileData?.content, isEditing])

  useEffect(() => {
    setIsEditing(false)
    setDraftContent(typeof fileData?.content === 'string' ? fileData.content : '')
    setCopied(false)
  }, [filePath])

  // 复制内容到剪贴板
  const handleCopy = useCallback(async () => {
    const textToCopy = isEditing ? draftContent : fileData?.content
    if (!textToCopy) return

    try {
      await navigator.clipboard.writeText(textToCopy)
      setCopied(true)
      showNotification('success', '内容已复制到剪贴板')
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      showNotification('error', '复制失败')
    }
  }, [draftContent, fileData?.content, isEditing])

  // 全屏切换
  const toggleFullscreen = () => {
    setIsFullscreen(!isFullscreen)
  }

  // 处理下载
  const handleDownload = () => {
    if (onDownload && fileName && typeof filePath === 'string') {
      onDownload(filePath, fileName)
    }
  }

  const handleEditorChange = useCallback((value?: string) => {
    setDraftContent(value ?? '')
  }, [])

  const handleStartEditing = useCallback(() => {
    if (!canEdit || typeof fileData?.content !== 'string') {
      return
    }
    setIsEditing(true)
    setDraftContent(fileData.content)
  }, [canEdit, fileData?.content])

  const handleCancelEditing = useCallback(() => {
    setIsEditing(false)
    setDraftContent(typeof fileData?.content === 'string' ? fileData.content : '')
  }, [fileData?.content])

  const handleSave = useCallback(async () => {
    if (!onSave || typeof filePath !== 'string') {
      return
    }
    try {
      const updated = await onSave(filePath, draftContent)
      setIsEditing(false)
      if (typeof updated?.content === 'string') {
        setDraftContent(updated.content)
      }
    } catch (err) {
      // 错误提示由上层处理
    }
  }, [draftContent, filePath, onSave])

  const handleCloseView = useCallback(() => {
    setIsEditing(false)
    setDraftContent('')
    onClose?.()
  }, [onClose])

  // ESC键退出全屏
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && isFullscreen) {
        setIsFullscreen(false)
      }
    }

    if (!isFullscreen) {
      return undefined
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [isFullscreen])

  if (loading) {
    return (
      <div className={`${styles['file-viewer-container']} ${className || ''}`}>
        <Spin tip="正在加载文件内容..." size="large">
          <div style={{ height: 400 }} />
        </Spin>
      </div>
    )
  }

  if (error) {
    return (
      <div className={`${styles['file-viewer-container']} ${className || ''}`}>
        <Alert
          message="加载失败"
          description={error}
          type="error"
          showIcon
          action={
            <Button size="small" onClick={handleCloseView}>
              关闭
            </Button>
          }
        />
      </div>
    )
  }

  if (!fileData || !fileName) {
    return (
      <div className={`${styles['file-viewer-container']} ${className || ''}`}>
        <Empty 
          description="请选择要预览的文件" 
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      </div>
    )
  }

  if (!fileData.is_text) {
    return (
      <div className={`${styles['file-viewer-container']} ${className || ''}`}>
        <Alert
          message="无法预览此文件"
          description="此文件类型不支持在线预览，请下载后查看"
          type="warning"
          showIcon
          action={
            <Space>
              <Button icon={<DownloadOutlined />} onClick={handleDownload}>
                下载文件
              </Button>
              <Button onClick={handleCloseView}>
                关闭
              </Button>
            </Space>
          }
        />
      </div>
    )
  }

  const syntaxTheme = isDark ? tomorrow : prism

  return (
    <div className={`${styles['file-viewer-container']} ${isFullscreen ? styles.fullscreen : ''} ${className || ''}`}>
      {/* 文件信息框 */}
      {fileName && fileData && (
        <div className={styles['file-info-panel']}>
          <Space size="large" wrap>
            <Space>
              <FileTextOutlined style={{ color: '#1890ff' }} />
              <Text strong>{fileName}</Text>
              <Tag color="blue">{language.toUpperCase()}</Tag>
            </Space>
            <Space split={<span style={{ color: '#d9d9d9' }}>|</span>}>
              <Text type="secondary">
                <strong>大小:</strong> {formatFileSize(fileData.size || 0)}
              </Text>
              {fileData.modified_time && (
                <Text type="secondary">
                  <strong>修改时间:</strong> {formatTime(fileData.modified_time)}
                </Text>
              )}
              <Text type="secondary">
                <strong>编码:</strong> {fileData.encoding || 'UTF-8'}
              </Text>
              <Text type="secondary">
                <strong>类型:</strong> {fileData.mime_type || 'text/plain'}
              </Text>
            </Space>
            <Space>
              {canEdit && !isEditing && (
                <Tooltip title="编辑文件">
                  <Button
                    icon={<EditOutlined />}
                    type="text"
                    size="small"
                    onClick={handleStartEditing}
                  />
                </Tooltip>
              )}
              {canEdit && isEditing && (
                <>
                  <Tooltip title="保存修改">
                    <Button
                      icon={<SaveOutlined />}
                      type="primary"
                      size="small"
                      onClick={handleSave}
                      loading={saving}
                    >
                      保存
                    </Button>
                  </Tooltip>
                  <Tooltip title="取消编辑">
                    <Button
                      icon={<CloseOutlined />}
                      type="text"
                      size="small"
                      onClick={handleCancelEditing}
                      disabled={saving}
                    />
                  </Tooltip>
                </>
              )}
              <Tooltip title={copied ? "已复制!" : "复制内容"}>
                <Button
                  icon={<CopyOutlined />}
                  type="text"
                  size="small"
                  onClick={handleCopy}
                  disabled={isEditing ? draftContent.length === 0 : !fileData?.content}
                />
              </Tooltip>
              <Tooltip title="下载文件">
                <Button
                  icon={<DownloadOutlined />}
                  type="text"
                  size="small"
                  onClick={handleDownload}
                  disabled={typeof filePath !== 'string'}
                />
              </Tooltip>
              <Tooltip title={isFullscreen ? "退出全屏" : "全屏查看"}>
                <Button
                  icon={isFullscreen ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
                  type="text"
                  size="small"
                  onClick={toggleFullscreen}
                />
              </Tooltip>
              {onClose && (
                <Button type="text" size="small" onClick={handleCloseView} disabled={saving}>
                  关闭
                </Button>
              )}
            </Space>
          </Space>
        </div>
      )}

      {/* 文件内容 */}
      <div className={styles['file-content']}>
        {isEditing ? (
          <React.Suspense
            fallback={
              <div style={{ height: editorHeight }}>
                <Spin tip="正在加载编辑器..." size="large">
                  <div style={{ height: '100%' }} />
                </Spin>
              </div>
            }
          >
          <LazyCodeEditor
            value={draftContent}
            language={language}
            height={editorHeight}
            onChange={handleEditorChange}
            options={{
              automaticLayout: true,
              minimap: { enabled: false },
              readOnly: saving,
              fontSize: 14,
              scrollBeyondLastLine: false
            }}
          />
          </React.Suspense>
        ) : fileData?.content ? (
          <div className={styles['syntax-highlighter-container']}>
            <SyntaxHighlighter
              language={language}
              style={syntaxTheme}
              showLineNumbers
              wrapLines
              customStyle={{
                margin: 0,
                borderRadius: '6px',
                fontSize: '14px',
                lineHeight: '1.5',
                minHeight: '400px'
              }}
              codeTagProps={{
                style: {
                  fontFamily: '"Monaco", "Menlo", "Ubuntu Mono", monospace'
                }
              }}
            >
              {fileData.content}
            </SyntaxHighlighter>
          </div>
        ) : (
          <Empty description="文件内容为空" />
        )}
      </div>
    </div>
  )
}

export default React.memo(FileViewer)
