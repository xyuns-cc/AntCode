import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  Input,
  Button,
  Spin,
  Alert,
  Empty,
  Modal,
  Dropdown,
  Tooltip,
  Badge
} from 'antd'
import showNotification from '@/utils/notification'
import {
  SearchOutlined,
  CloseOutlined,
  FileTextOutlined,
  FolderOutlined,
  FolderOpenOutlined,
  RightOutlined,
  ExclamationCircleOutlined,
  CloudUploadOutlined,
  UndoOutlined,
  HistoryOutlined,
  DownOutlined,
  CheckCircleOutlined,
  DownloadOutlined
} from '@ant-design/icons'
import projectService from '@/services/projects'
import versionService from '@/services/versions'
import Logger from '@/utils/logger'
import { useThemeContext } from '@/contexts/ThemeContext'
import Editor from '@monaco-editor/react'
import type * as Monaco from 'monaco-editor'
import { 
  getMonacoLanguage, 
  getEditorOptions, 
  getMonacoTheme,
  configureMonaco 
} from '@/utils/monacoConfig'
import type { Project, ProjectFileContent, ProjectFileStructure, ProjectFileNode } from '@/types'
import type { EditStatus, ProjectVersion } from '@/types/version'
import './ProjectFileManager.css'

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

// 语言映射
const getLanguageFromMimeType = (mimeType: string, fileName: string): string => {
  const ext = fileName.split('.').pop()?.toLowerCase()
  
  const extMap: Record<string, string> = {
    'py': 'python', 'js': 'javascript', 'jsx': 'jsx', 'ts': 'typescript',
    'tsx': 'tsx', 'json': 'json', 'xml': 'xml', 'html': 'html',
    'css': 'css', 'scss': 'scss', 'md': 'markdown', 'yml': 'yaml', 'yaml': 'yaml',
    'sh': 'bash', 'bash': 'bash', 'sql': 'sql', 'go': 'go', 'java': 'java',
    'cpp': 'cpp', 'c': 'c', 'rs': 'rust', 'php': 'php', 'rb': 'ruby'
  }
  
  if (ext && extMap[ext]) return extMap[ext]
  if (mimeType.includes('python')) return 'python'
  if (mimeType.includes('javascript')) return 'javascript'
  if (mimeType.includes('json')) return 'json'
  return 'text'
}

// 判断文件是否可以在线预览
const isFilePreviewable = (fileName: string): boolean => {
  const ext = fileName.split('.').pop()?.toLowerCase()
  
  // 可预览的文本文件扩展名
  const previewableExts = [
    // 编程语言
    'py', 'js', 'jsx', 'ts', 'tsx', 'java', 'c', 'cpp', 'h', 'hpp',
    'cs', 'php', 'rb', 'go', 'rs', 'swift', 'kt', 'scala', 'dart',
    // 前端
    'html', 'htm', 'css', 'scss', 'sass', 'less', 'vue',
    // 数据格式
    'json', 'xml', 'yml', 'yaml', 'toml', 'ini', 'cfg', 'conf',
    // 脚本
    'sh', 'bash', 'zsh', 'bat', 'cmd', 'ps1',
    // 文档
    'txt', 'md', 'markdown', 'rst', 'log',
    // 其他
    'sql', 'dockerfile', 'gitignore', 'env', 'properties'
  ]
  
  return ext ? previewableExts.includes(ext) : false
}

// 获取不可预览文件的类型描述
const getFileTypeDescription = (fileName: string): string => {
  const ext = fileName.split('.').pop()?.toLowerCase()
  
  const typeMap: Record<string, string> = {
    // 可执行文件
    'exe': 'Windows可执行程序',
    'app': 'macOS应用程序',
    'dmg': 'macOS磁盘镜像',
    'msi': 'Windows安装程序',
    'apk': 'Android应用包',
    'deb': 'Debian软件包',
    'rpm': 'RPM软件包',
    // 压缩文件
    'zip': 'ZIP压缩文件',
    'rar': 'RAR压缩文件',
    'tar': 'TAR归档文件',
    'gz': 'GZIP压缩文件',
    '7z': '7-Zip压缩文件',
    // 图片
    'jpg': 'JPEG图片',
    'jpeg': 'JPEG图片',
    'png': 'PNG图片',
    'gif': 'GIF动图',
    'bmp': 'BMP图片',
    'svg': 'SVG矢量图',
    'webp': 'WebP图片',
    'ico': '图标文件',
    // 视频
    'mp4': 'MP4视频',
    'avi': 'AVI视频',
    'mov': 'MOV视频',
    'wmv': 'WMV视频',
    'flv': 'FLV视频',
    'mkv': 'MKV视频',
    // 音频
    'mp3': 'MP3音频',
    'wav': 'WAV音频',
    'flac': 'FLAC音频',
    'aac': 'AAC音频',
    // 文档
    'pdf': 'PDF文档',
    'doc': 'Word文档',
    'docx': 'Word文档',
    'xls': 'Excel表格',
    'xlsx': 'Excel表格',
    'ppt': 'PowerPoint演示',
    'pptx': 'PowerPoint演示',
    // 数据库
    'db': '数据库文件',
    'sqlite': 'SQLite数据库',
    'sqlite3': 'SQLite数据库',
    // 其他二进制
    'bin': '二进制文件',
    'dll': '动态链接库',
    'so': '共享库文件',
    'dylib': '动态库文件',
    'jar': 'Java归档文件',
    'war': 'Web应用归档',
    'class': 'Java字节码'
  }
  
  return ext && typeMap[ext] ? typeMap[ext] : '二进制文件'
}

interface ProjectFileManagerProps {
  className?: string
}

interface FileNode {
  name: string
  path: string
  type: 'file' | 'folder'
  size?: number
  children?: FileNode[]
  depth?: number
}

interface SelectedFileState {
  path: string
  name: string
  data: ProjectFileContent
}

const ProjectFileManager: React.FC<ProjectFileManagerProps> = ({ className }) => {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const projectId = id || ''
  const { isDark } = useThemeContext()

  // 状态管理
  const [project, setProject] = useState<Project | null>(null)
  const [fileStructure, setFileStructure] = useState<ProjectFileStructure | null>(null)
  const [selectedFile, setSelectedFile] = useState<SelectedFileState | null>(null)
  const [loading, setLoading] = useState(true)
  const [fileLoading, setFileLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saveLoading, setSaveLoading] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState('')
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(new Set())
  const [fileModifiedTime, setFileModifiedTime] = useState<number | null>(null)  // 文件修改时间戳
  const [fileHistory, setFileHistory] = useState<Array<{path: string, name: string, timestamp: number}>>([])  // 文件操作历史
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)  // 是否有未保存的更改
  const [originalContent, setOriginalContent] = useState<string>('')  // 原始文件内容，用于比较
  
  // 版本管理状态
  const [editStatus, setEditStatus] = useState<EditStatus | null>(null)
  const [versions, setVersions] = useState<ProjectVersion[]>([])
  const [_currentVersion, setCurrentVersion] = useState<string>('draft')
  const [publishLoading, setPublishLoading] = useState(false)
  const [showVersionHistory, setShowVersionHistory] = useState(false)
  
  // Monaco Editor 相关
  const editorRef = useRef<Monaco.editor.IStandaloneCodeEditor | null>(null)
  const monacoRef = useRef<typeof Monaco | null>(null)

  // 加载文件历史
  useEffect(() => {
    const historyKey = `file_history_${projectId}`
    const savedHistory = localStorage.getItem(historyKey)
    if (savedHistory) {
      try {
        setFileHistory(JSON.parse(savedHistory))
      } catch (err) {
        Logger.error('加载文件历史失败:', err)
      }
    }
  }, [projectId])

  // 添加到文件历史
  const addToHistory = useCallback((path: string, name: string) => {
    const newHistory = [
      { path, name, timestamp: Date.now() },
      ...fileHistory.filter(item => item.path !== path)
    ].slice(0, 10)  // 只保留最近10个
    
    setFileHistory(newHistory)
    
    // 保存到localStorage
    const historyKey = `file_history_${projectId}`
    try {
      localStorage.setItem(historyKey, JSON.stringify(newHistory))
    } catch (err) {
      Logger.error('保存文件历史失败:', err)
    }
  }, [fileHistory, projectId])

  // 从历史记录中移除
  const removeFromHistory = useCallback((path: string, e: React.MouseEvent) => {
    e.stopPropagation()  // 阻止触发打开文件
    
    const newHistory = fileHistory.filter(item => item.path !== path)
    setFileHistory(newHistory)
    
    // 保存到localStorage
    const historyKey = `file_history_${projectId}`
    try {
      localStorage.setItem(historyKey, JSON.stringify(newHistory))
    } catch (err) {
      Logger.error('保存文件历史失败:', err)
    }
  }, [fileHistory, projectId])

  // 搜索防抖
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearchQuery(searchQuery)
    }, 300) // 300ms 防抖延迟
    
    return () => clearTimeout(timer)
  }, [searchQuery])

  // 扁平化文件树
  const flatFileList = useMemo(() => {
    if (!fileStructure) {
      return []
    }

    const root = fileStructure.structure

    // 如果是单个文件
    if (root.type === 'file') {
      return [{
        name: root.name,
        path: root.path,
        type: 'file',
        size: root.size,
        depth: 0
      }]
    }

    const flatten = (node: ProjectFileNode, depth = 0): FileNode[] => {
      const result: FileNode[] = []
      
      const fileNode: FileNode = {
        name: node.name,
        path: node.path,
        type: node.type === 'directory' ? 'folder' : 'file',
        size: node.size,
        depth
      }

      result.push(fileNode)

      if (node.type === 'directory' && node.children.length > 0) {
        node.children.forEach((child) => {
          result.push(...flatten(child, depth + 1))
        })
      }

      return result
    }

    return root.children.flatMap((child) => flatten(child, 0))
  }, [fileStructure])

  // 过滤后的文件列表（使用防抖后的搜索词）
  const filteredFiles = useMemo(() => {
    if (!debouncedSearchQuery.trim()) return flatFileList
    
    const query = debouncedSearchQuery.toLowerCase()
    return flatFileList.filter(file => 
      file.name.toLowerCase().includes(query) ||
      file.path.toLowerCase().includes(query)  // 同时搜索路径
    )
  }, [flatFileList, debouncedSearchQuery])

  // 高亮文本组件
  const HighlightText = useCallback(({ text, highlight }: { text: string, highlight: string }) => {
    if (!highlight.trim()) {
      return <>{text}</>
    }
    
    const parts = text.split(new RegExp(`(${highlight})`, 'gi'))
    return (
      <>
        {parts.map((part, index) => 
          part.toLowerCase() === highlight.toLowerCase() ? (
            <mark key={index} style={{ 
              backgroundColor: 'var(--primary-color, #1890ff)', 
              color: '#fff',
              padding: '0 2px',
              borderRadius: '2px',
              fontWeight: 500
            }}>
              {part}
            </mark>
          ) : (
            <span key={index}>{part}</span>
          )
        )}
      </>
    )
  }, [])

  // 加载项目信息
  const loadProject = useCallback(async () => {
    try {
      const projectData = await projectService.getProject(projectId)
      setProject(projectData)
    } catch (err) {
      Logger.error('加载项目信息失败:', err)
      setError('加载项目信息失败')
    }
  }, [projectId])

  // 下载文件
  const handleDownloadFile = useCallback(async (filePath: string, fileName: string) => {
    try {
      const blob = await projectService.downloadProjectFile(projectId, filePath)
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = fileName
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)
    } catch (err) {
      Logger.error('下载文件失败:', err)
    }
  }, [projectId])

  // 预览文件
  const handlePreviewFile = useCallback(async (filePath: string, fileName: string) => {
    // 检查文件是否可预览
    if (!isFilePreviewable(fileName)) {
      const fileType = getFileTypeDescription(fileName)
      // 不发送请求，直接设置一个特殊的状态用于显示提示
      setSelectedFile({
        path: filePath,
        name: fileName,
        data: {
          name: fileName,
          path: filePath,
          size: 0,
          modified_time: 0,
          mime_type: 'application/octet-stream',
          is_text: false,
          binary: true,
          content: '',
          file_type_description: fileType // 自定义字段
        } as ProjectFileContent & { file_type_description: string }
      })
      return
    }
    
    try {
      setFileLoading(true)
      const fileContent = await projectService.getFileContent(projectId, filePath)
      setSelectedFile({
        path: filePath,
        name: fileName,
        data: fileContent
      })
      // 保存原始内容用于比较
      setOriginalContent(fileContent.content || '')
      // 保存文件修改时间戳用于冲突检测
      setFileModifiedTime(fileContent.modified_time || Date.now())
      // 添加到历史记录
      addToHistory(filePath, fileName)
      // 重置未保存标志
      setHasUnsavedChanges(false)
    } catch (err) {
      Logger.error('预览文件失败:', err)
      showNotification('error', '预览文件失败')
    } finally {
      setFileLoading(false)
    }
  }, [projectId, addToHistory])

  // 加载文件结构
  const loadFileStructure = useCallback(async () => {
    try {
      setLoading(true)
      const structure = await projectService.getProjectFileStructure(projectId)
      setFileStructure(structure)
      setError(null)
      
      // 如果是单个文件，自动加载并预览
      if (structure.structure.type === 'file') {
        // 单个文件：传文件名作为路径（后端会自动判断）
        try {
          const fileContent = await projectService.getFileContent(projectId, structure.structure.path)
          setSelectedFile({
            path: structure.structure.path,
            name: structure.structure.name,
            data: fileContent
          })
          // 保存原始内容
          setOriginalContent(fileContent.content || '')
          // 保存文件修改时间戳
          setFileModifiedTime(fileContent.modified_time || Date.now())
        } catch (_err) {
          Logger.error('预览单个文件失败:', _err)
        }
      } else {
        // 默认展开根目录的第一层文件夹
        const rootFolders = structure.structure.children
          .filter((node) => node.type === 'directory')
          .map((node) => node.path)
        setExpandedFolders(new Set(rootFolders))
      }
    } catch (err) {
      Logger.error('加载文件结构失败:', err)
      setError('加载文件结构失败，可能项目没有上传文件或文件已被删除')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  // 加载编辑状态
  const loadEditStatus = useCallback(async () => {
    try {
      const status = await versionService.getEditStatus(projectId)
      setEditStatus(status)
    } catch (err) {
      Logger.error('加载编辑状态失败:', err)
    }
  }, [projectId])

  // 加载版本列表
  const loadVersions = useCallback(async () => {
    try {
      const result = await versionService.getVersions(projectId)
      setVersions(result.versions || [])
    } catch (err) {
      Logger.error('加载版本列表失败:', err)
    }
  }, [projectId])

  // 丢弃草稿
  const handleDiscard = useCallback(async () => {
    if (!hasUnsavedChanges && !editStatus?.dirty) {
      showNotification('info', '没有需要丢弃的修改')
      return
    }

    Modal.confirm({
      title: '丢弃修改',
      icon: <ExclamationCircleOutlined style={{ color: '#faad14' }} />,
      content: (
        <div style={{ marginTop: 16 }}>
          <p style={{ fontWeight: 500, color: '#ff4d4f' }}>确定要丢弃所有未发布的修改吗？</p>
          <p style={{ color: '#666', fontSize: '13px' }}>
            此操作将恢复到最新已发布版本，所有未保存的修改将丢失。
          </p>
        </div>
      ),
      okText: '丢弃',
      okType: 'danger',
      cancelText: '取消',
      centered: true,
      onOk: async () => {
        try {
          await versionService.discard(projectId)
          showNotification('success', '已恢复到最新版本')
          
          // 刷新
          setHasUnsavedChanges(false)
          setSelectedFile(null)
          await loadFileStructure()
          await loadEditStatus()
        } catch (err) {
          Logger.error('丢弃失败:', err)
          showNotification('error', '丢弃失败')
        }
      }
    })
  }, [projectId, hasUnsavedChanges, editStatus, loadFileStructure, loadEditStatus])

  // 回滚到指定版本
  const handleRollback = useCallback(async (version: number) => {
    Modal.confirm({
      title: '回滚版本',
      icon: <UndoOutlined style={{ color: '#faad14' }} />,
      content: (
        <div style={{ marginTop: 16 }}>
          <p>确定要回滚到版本 v{version} 吗？</p>
          <p style={{ color: '#666', fontSize: '13px' }}>
            回滚后草稿将被替换为该版本的内容，需要重新发布才能生效。
          </p>
        </div>
      ),
      okText: '回滚',
      okType: 'danger',
      cancelText: '取消',
      centered: true,
      onOk: async () => {
        try {
          await versionService.rollback(projectId, { version })
          showNotification('success', `已回滚到版本 v${version}`)
          
          // 刷新
          setSelectedFile(null)
          setCurrentVersion('draft')
          await loadFileStructure()
          await loadEditStatus()
        } catch (err) {
          Logger.error('回滚失败:', err)
          showNotification('error', '回滚失败')
        }
      }
    })
  }, [projectId, loadFileStructure, loadEditStatus])

  // 保存文件
  const handleSaveFile = useCallback(async () => {
    if (!selectedFile) return
    
    // 检查文件是否可编辑
    if (!selectedFile.data.is_text) {
      showNotification('warning', '该文件不支持编辑')
      return
    }
    
    // 检查内容是否为空
    if (selectedFile.data.content === undefined || selectedFile.data.content === null) {
      showNotification('warning', '文件内容不能为空')
      return
    }
    
    try {
      setSaveLoading(true)
      
      // 冲突检测：保存前先获取最新文件信息
      try {
        const latestFile = await projectService.getFileContent(projectId, selectedFile.path)
        
        // 比较修改时间
        if (fileModifiedTime && latestFile.modified_time && 
            latestFile.modified_time > fileModifiedTime) {
          // 文件已被修改，使用 Modal 询问用户
          const confirmed = await new Promise<boolean>((resolve) => {
            Modal.confirm({
              title: '文件冲突警告',
              icon: <ExclamationCircleOutlined style={{ color: '#ff4d4f' }} />,
              content: (
                <div style={{ marginTop: 16 }}>
                  <p style={{ marginBottom: 12, fontWeight: 500 }}>文件已被其他用户或进程修改！</p>
                  <div style={{ 
                    backgroundColor: '#fff7e6', 
                    border: '1px solid #ffd591',
                    borderRadius: '4px',
                    padding: '12px',
                    fontSize: '13px'
                  }}>
                    <div style={{ marginBottom: 8 }}>
                      <span style={{ color: '#666' }}>原修改时间：</span>
                      <span style={{ color: '#000', fontWeight: 500 }}>
                        {new Date(fileModifiedTime * 1000).toLocaleString('zh-CN')}
                      </span>
                    </div>
                    <div>
                      <span style={{ color: '#666' }}>最新修改时间：</span>
                      <span style={{ color: '#ff4d4f', fontWeight: 500 }}>
                        {new Date(latestFile.modified_time * 1000).toLocaleString('zh-CN')}
                      </span>
                    </div>
                  </div>
                  <p style={{ marginTop: 12, color: '#666', fontSize: '14px' }}>
                    是否仍要覆盖保存？
                  </p>
                </div>
              ),
              okText: '覆盖保存',
              okType: 'danger',
              cancelText: '取消保存',
              centered: true,
              width: 480,
              onOk: () => resolve(true),
              onCancel: () => resolve(false)
            })
          })
          
          if (!confirmed) {
            showNotification('info', '已取消保存操作')
            setSaveLoading(false)
            return
          }
        }
      } catch (checkErr) {
        Logger.warn('冲突检测失败，继续保存:', checkErr)
      }
      
      const result = await projectService.updateFileContent(projectId, {
        file_path: selectedFile.path,
        content: selectedFile.data.content,
        encoding: selectedFile.data.encoding || 'utf-8'
      })
      
      // 更新本地状态和时间戳
      setSelectedFile({
        ...selectedFile,
        data: result
      })
      setFileModifiedTime(result.modified_time || Date.now())
      
      // 更新原始内容为最新保存的内容
      setOriginalContent(result.content || '')
      
      showNotification('success', '文件保存成功')
      
      // 重置未保存标志
      setHasUnsavedChanges(false)
      
      // 后台刷新文件结构（不阻塞用户）
      loadFileStructure().catch(err => {
        Logger.error('刷新文件结构失败:', err)
      })
    } catch (err: unknown) {
      Logger.error('保存文件失败:', err)
      const errObj = err as { response?: { data?: { message?: string } }; message?: string }
      const errorMsg = errObj?.response?.data?.message || errObj?.message || '保存文件失败'
      showNotification('error', errorMsg)
      throw err  // 抛出错误，让调用者知道保存失败
    } finally {
      setSaveLoading(false)
    }
  }, [projectId, selectedFile, fileModifiedTime, loadFileStructure])

  // 发布草稿
  const handlePublish = useCallback(async () => {
    if (!hasUnsavedChanges && !editStatus?.dirty) {
      showNotification('info', '没有需要发布的修改')
      return
    }

    Modal.confirm({
      title: '发布版本',
      icon: <CloudUploadOutlined style={{ color: '#1890ff' }} />,
      content: (
        <div style={{ marginTop: 16 }}>
          <p>确定要将当前草稿发布为新版本吗？</p>
          <p style={{ color: '#666', fontSize: '13px' }}>
            发布后将创建不可变的版本快照，可用于执行和回滚。
          </p>
        </div>
      ),
      okText: '发布',
      cancelText: '取消',
      centered: true,
      onOk: async () => {
        try {
          setPublishLoading(true)

          // 如果有未保存的本地修改，先保存
          if (hasUnsavedChanges && selectedFile) {
            await handleSaveFile()
          }

          const result = await versionService.publish(projectId, {
            description: `版本 v${(editStatus?.published_version || 0) + 1}`
          })

          showNotification('success', `版本 v${result.version} 发布成功`)

          // 刷新状态
          setHasUnsavedChanges(false)
          await loadEditStatus()
          await loadVersions()
        } catch (err) {
          Logger.error('发布失败:', err)
          showNotification('error', '发布失败')
        } finally {
          setPublishLoading(false)
        }
      }
    })
  }, [projectId, hasUnsavedChanges, editStatus, selectedFile, handleSaveFile, loadEditStatus, loadVersions])

  // 刷新
  const handleRefresh = useCallback(() => {
    setSelectedFile(null)
    setSearchQuery('')
    setDebouncedSearchQuery('')
    loadFileStructure()
  }, [loadFileStructure])

  // 关闭预览
  const handleClosePreview = () => {
    // 检查是否有未保存的更改
    if (hasUnsavedChanges) {
      Modal.confirm({
        title: '未保存的更改',
        icon: <ExclamationCircleOutlined style={{ color: '#faad14' }} />,
        content: (
          <div style={{ marginTop: 16 }}>
            <p style={{ marginBottom: 8 }}>当前文件有未保存的更改！</p>
            <p style={{ color: '#666', fontSize: '14px' }}>是否保存更改？</p>
          </div>
        ),
        okText: '保存',
        cancelText: '放弃更改',
        centered: true,
        onOk: () => {
          handleSaveFile()
        },
        onCancel: () => {
          setSelectedFile(null)
          setHasUnsavedChanges(false)
        }
      })
      return
    }
    
    setSelectedFile(null)
    setHasUnsavedChanges(false)
  }

  // 关闭文件管理器（返回项目详情）
  const handleCloseManager = useCallback(() => {
    // 检查是否有未保存的本地更改
    if (hasUnsavedChanges) {
      Modal.confirm({
        title: '未保存的更改',
        icon: <ExclamationCircleOutlined style={{ color: '#faad14' }} />,
        content: (
          <div style={{ marginTop: 16 }}>
            <p style={{ marginBottom: 8 }}>当前文件有未保存的更改！</p>
            <p style={{ color: '#666', fontSize: '14px' }}>是否保存更改？</p>
          </div>
        ),
        okText: '保存并退出',
        cancelText: '放弃更改',
        centered: true,
        onOk: async () => {
          try {
            await handleSaveFile()
            // 保存后检查是否需要发布
            if (editStatus?.dirty) {
              Modal.confirm({
                title: '未发布的修改',
                icon: <CloudUploadOutlined style={{ color: '#1890ff' }} />,
                content: '草稿已保存，是否发布为新版本？',
                okText: '发布并退出',
                cancelText: '稍后发布',
                centered: true,
                onOk: async () => {
                  try {
                    await versionService.publish(projectId)
                    navigate(`/projects/${projectId}`)
                  } catch (err) {
                    Logger.error('发布失败:', err)
                    navigate(`/projects/${projectId}`)
                  }
                },
                onCancel: () => {
                  navigate(`/projects/${projectId}`)
                }
              })
            } else {
              navigate(`/projects/${projectId}`)
            }
          } catch (_err) {
            Logger.error('保存失败，取消退出')
          }
        },
        onCancel: () => {
          navigate(`/projects/${projectId}`)
        }
      })
      return
    }
    
    // 检查是否有未发布的草稿修改
    if (editStatus?.dirty) {
      Modal.confirm({
        title: '未发布的修改',
        icon: <CloudUploadOutlined style={{ color: '#1890ff' }} />,
        content: (
          <div style={{ marginTop: 16 }}>
            <p style={{ marginBottom: 8 }}>草稿有未发布的修改！</p>
            <p style={{ color: '#666', fontSize: '14px' }}>是否发布为新版本？</p>
          </div>
        ),
        okText: '发布并退出',
        cancelText: '稍后发布',
        centered: true,
        onOk: async () => {
          try {
            await versionService.publish(projectId)
            showNotification('success', '版本发布成功')
            navigate(`/projects/${projectId}`)
          } catch (err) {
            Logger.error('发布失败:', err)
            navigate(`/projects/${projectId}`)
          }
        },
        onCancel: () => {
          navigate(`/projects/${projectId}`)
        }
      })
      return
    }
    
    // 无未保存更改，直接退出
    navigate(`/projects/${projectId}`)
  }, [hasUnsavedChanges, editStatus, handleSaveFile, navigate, projectId])

  // 处理文件内容修改
  const handleContentChange = useCallback((newContent: string) => {
    if (!selectedFile) return

    setSelectedFile({
      ...selectedFile,
      data: {
        ...selectedFile.data,
        content: newContent
      }
    })
    
    // 比较当前内容和原始内容，判断是否真的有修改
    setHasUnsavedChanges(newContent !== originalContent)
  }, [selectedFile, originalContent])
  
  // Monaco Editor 挂载时的回调
  const handleEditorDidMount = useCallback((editor: Monaco.editor.IStandaloneCodeEditor, monaco: typeof Monaco) => {
    editorRef.current = editor
    monacoRef.current = monaco
    
    // 配置 Monaco
    configureMonaco(monaco)
    
    // 添加保存快捷键 (Ctrl+S / Cmd+S)
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      if (hasUnsavedChanges && selectedFile) {
        handleSaveFile()
      }
    })
    
    // 焦点到编辑器
    editor.focus()
  }, [hasUnsavedChanges, selectedFile, handleSaveFile])
  
  // Monaco Editor 内容变化时的回调
  const handleEditorChange = useCallback((value: string | undefined) => {
    if (value !== undefined) {
      handleContentChange(value)
    }
  }, [handleContentChange])

  // 切换文件夹展开状态
  const toggleFolder = (path: string) => {
    const newExpanded = new Set(expandedFolders)
    if (newExpanded.has(path)) {
      newExpanded.delete(path)
    } else {
      newExpanded.add(path)
    }
    setExpandedFolders(newExpanded)
  }

  const buildFileNode = (n: ProjectFileNode, depth = 0): FileNode => {
    const fileNode: FileNode = {
      name: n.name,
      path: n.path,
      type: n.type === 'directory' ? 'folder' : 'file',
      size: n.size,
      depth
    }

    if (n.type === 'directory' && n.children.length > 0) {
      fileNode.children = n.children.map((child) => buildFileNode(child, depth + 1))
    }

    return fileNode
  }

  // 渲染文件树节点
  const renderTreeNode = (node: FileNode, isVisible: boolean) => {
    if (node.type === 'folder') {
      const isExpanded = expandedFolders.has(node.path)
      return (
        <div key={node.path}>
          <div
            className="tree-node folder"
            style={{ 
              paddingLeft: `${(node.depth || 0) * 16 + 12}px`,
              display: isVisible ? 'flex' : 'none'
            }}
            onClick={() => toggleFolder(node.path)}
          >
            <span className={`node-arrow ${isExpanded ? 'expanded' : ''}`}>
              <RightOutlined />
            </span>
            <span className="node-icon icon-folder">
              {isExpanded ? <FolderOpenOutlined /> : <FolderOutlined />}
            </span>
            <span className="node-name">{node.name}</span>
          </div>
          {node.children && node.children.map(child => 
            renderTreeNode(child, isVisible && isExpanded)
          )}
        </div>
      )
    }
    
    return (
      <div
        key={node.path}
        className={`tree-node file ${selectedFile?.path === node.path ? 'selected' : ''}`}
        style={{ 
          paddingLeft: `${(node.depth || 0) * 16 + 12}px`,
          display: isVisible ? 'flex' : 'none'
        }}
        onClick={() => handlePreviewFile(node.path, node.name)}
      >
        <span className="node-icon icon-file">
          <FileTextOutlined />
        </span>
        <span className="node-name">{node.name}</span>
        {node.size !== undefined && (
          <span className="node-size">{formatFileSize(node.size)}</span>
        )}
      </div>
    )
  }

  // 初始化
  useEffect(() => {
    if (projectId) {
      loadProject()
      loadFileStructure()
      loadEditStatus()
      loadVersions()
    }
  }, [projectId, loadProject, loadFileStructure, loadEditStatus, loadVersions])

  // 按ESC键关闭
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleCloseManager()
      }
    }
    window.addEventListener('keydown', handleEsc)
    return () => window.removeEventListener('keydown', handleEsc)
  }, [handleCloseManager])

  if (!projectId) {
    return (
      <div className="file-manager-error">
        <Alert
          message="项目ID无效"
          description="请检查URL中的项目ID是否正确"
          type="error"
          showIcon
          action={
            <Button onClick={() => navigate('/projects')}>
              返回项目列表
            </Button>
          }
        />
      </div>
    )
  }

  const language = selectedFile ? getLanguageFromMimeType(
    selectedFile.data.mime_type || '', 
    selectedFile.name
  ) : 'text'

  return (
    <div className={`file-manager-modal ${isDark ? 'dark-theme' : ''} ${className || ''}`}>
      {/* 背景装饰 */}
      <div className="background-decoration">
        <div className="decoration-circle"></div>
        <div className="decoration-circle"></div>
      </div>

      {/* 主窗口 */}
      <div className="window-container">
        {/* 窗口标题栏 */}
        <header className="window-header no-select">
          <div className="window-title">
            <span>{project?.name || '文件浏览器'}</span>
            {/* 编辑状态指示器 */}
            {(hasUnsavedChanges || editStatus?.dirty) && (
              <Badge 
                status="warning" 
                text={<span style={{ fontSize: '12px', color: '#faad14' }}>未发布</span>}
                style={{ marginLeft: 12 }}
              />
            )}
            {editStatus && editStatus.published_version > 0 && (
              <span style={{ 
                marginLeft: 12, 
                fontSize: '12px', 
                color: 'var(--text-secondary)',
                background: 'var(--bg-secondary)',
                padding: '2px 8px',
                borderRadius: '4px'
              }}>
                v{editStatus.published_version}
              </span>
            )}
          </div>
          
          <div className="header-actions">
            {/* 版本历史按钮 */}
            <Dropdown
              trigger={['click']}
              open={showVersionHistory}
              onOpenChange={setShowVersionHistory}
              dropdownRender={() => (
                <div style={{
                  background: 'var(--bg-primary)',
                  borderRadius: '8px',
                  boxShadow: '0 6px 16px rgba(0,0,0,0.12)',
                  padding: '8px 0',
                  minWidth: '280px',
                  maxHeight: '400px',
                  overflow: 'auto'
                }}>
                  <div style={{ 
                    padding: '8px 16px', 
                    borderBottom: '1px solid var(--border-color)',
                    fontWeight: 500
                  }}>
                    版本历史
                  </div>
                  {versions.length === 0 ? (
                    <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-secondary)' }}>
                      暂无已发布版本
                    </div>
                  ) : (
                    versions.map((v) => (
                      <div 
                        key={v.version_id}
                        style={{
                          padding: '12px 16px',
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          borderBottom: '1px solid var(--border-color)'
                        }}
                        onMouseEnter={(e) => e.currentTarget.style.background = 'var(--hover-bg)'}
                        onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                      >
                        <div>
                          <div style={{ fontWeight: 500 }}>
                            v{v.version}
                            {v.version === editStatus?.published_version && (
                              <CheckCircleOutlined style={{ marginLeft: 8, color: '#52c41a', fontSize: '12px' }} />
                            )}
                          </div>
                          <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                            {new Date(v.created_at).toLocaleString('zh-CN')}
                          </div>
                          <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
                            {v.file_count} 文件 · {(v.total_size / 1024).toFixed(1)} KB
                          </div>
                        </div>
                        <Tooltip title="回滚到此版本">
                          <Button 
                            size="small" 
                            icon={<UndoOutlined />}
                            onClick={(e) => {
                              e.stopPropagation()
                              setShowVersionHistory(false)
                              handleRollback(v.version)
                            }}
                          />
                        </Tooltip>
                      </div>
                    ))
                  )}
                </div>
              )}
            >
              <button className="action-button secondary">
                <HistoryOutlined style={{ marginRight: 4 }} />
                历史
                <DownOutlined style={{ marginLeft: 4, fontSize: '10px' }} />
              </button>
            </Dropdown>

            <button 
              className="action-button secondary"
              onClick={handleRefresh}
              disabled={loading}
            >
              <svg className="icon" viewBox="0 0 24 24">
                <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>
                <path d="M21 3v5h-5"/>
                <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>
                <path d="M3 21v-5h5"/>
              </svg>
              重置
            </button>
            
            <button 
              className="action-button"
              onClick={handleSaveFile}
              disabled={!selectedFile || !selectedFile.data.is_text || saveLoading}
            >
              <svg className="icon" viewBox="0 0 24 24">
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
                <polyline points="17 21 17 13 7 13 7 21"/>
                <polyline points="7 3 7 8 15 8"/>
              </svg>
              保存
            </button>

            {/* 丢弃按钮 */}
            <Tooltip title="丢弃所有未发布的修改">
              <button 
                className="action-button secondary"
                onClick={handleDiscard}
                disabled={!hasUnsavedChanges && !editStatus?.dirty}
              >
                <UndoOutlined style={{ marginRight: 4 }} />
                丢弃
              </button>
            </Tooltip>

            {/* 发布按钮 */}
            <button 
              className="action-button primary"
              onClick={handlePublish}
              disabled={publishLoading || (!hasUnsavedChanges && !editStatus?.dirty)}
              style={{
                background: (hasUnsavedChanges || editStatus?.dirty) ? '#1890ff' : undefined,
                color: (hasUnsavedChanges || editStatus?.dirty) ? '#fff' : undefined
              }}
            >
              <CloudUploadOutlined style={{ marginRight: 4 }} />
              {publishLoading ? '发布中...' : '发布'}
            </button>
            
            <div className="window-controls">
              <button 
                className="window-control close" 
                title="关闭"
                onClick={handleCloseManager}
              >
                <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M4 4l8 8M12 4l-8 8"/>
                </svg>
              </button>
            </div>
          </div>
        </header>

        {/* 窗口内容 */}
        <div className="window-content">
          {/* 侧边栏 */}
          <aside className="sidebar">
            {/* 搜索栏 */}
            <div className="search-container">
              <div className="search-field">
                <SearchOutlined className="search-icon" />
                <Input
                  className="search-input"
                  placeholder="搜索文件"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  allowClear
                />
              </div>
              {/* 搜索结果统计 */}
              {debouncedSearchQuery && (
                <div style={{ 
                  padding: '8px 16px', 
                  fontSize: '12px', 
                  color: 'var(--text-secondary)',
                  borderBottom: '1px solid var(--border-color)'
                }}>
                  找到 <strong style={{ color: 'var(--primary-color)' }}>{filteredFiles.length}</strong> 个匹配项
                </div>
              )}
              {/* 最近打开文件 */}
              {!searchQuery && fileHistory.length > 0 && (
                <div style={{ 
                  padding: '8px 16px', 
                  borderBottom: '1px solid var(--border-color)'
                }}>
                  <div style={{ 
                    fontSize: '11px', 
                    color: 'var(--text-tertiary)',
                    marginBottom: '6px',
                    fontWeight: 500
                  }}>
                    最近打开
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
                    {fileHistory.slice(0, 5).map((item, index) => (
                      <div
                        key={index}
                        onClick={() => handlePreviewFile(item.path, item.name)}
                        style={{
                          fontSize: '12px',
                          padding: '4px 8px',
                          borderRadius: '4px',
                          cursor: 'pointer',
                          color: 'var(--text-secondary)',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                          transition: 'all 0.2s',
                          position: 'relative'
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.backgroundColor = 'var(--hover-bg, rgba(0,0,0,0.04))'
                          e.currentTarget.style.color = 'var(--text-primary)'
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.backgroundColor = 'transparent'
                          e.currentTarget.style.color = 'var(--text-secondary)'
                        }}
                      >
                        <FileTextOutlined style={{ fontSize: '12px', flexShrink: 0 }} />
                        <span style={{ 
                          flex: 1,
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap'
                        }}>
                          {item.name}
                        </span>
                        <CloseOutlined 
                          style={{ 
                            fontSize: '10px',
                            flexShrink: 0,
                            opacity: 0.5,
                            transition: 'opacity 0.2s'
                          }}
                          onClick={(e) => removeFromHistory(item.path, e)}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.opacity = '1'
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.opacity = '0.5'
                          }}
                        />
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
            
            {/* 文件树 */}
            <div className="file-tree">
              {loading ? (
                <div style={{ padding: '40px 20px', textAlign: 'center' }}>
                  <Spin tip="加载中...">
                    <div style={{ height: 100 }} />
                  </Spin>
                </div>
              ) : error ? (
                <div style={{ padding: '20px' }}>
                  <Alert message={error} type="error" showIcon />
                </div>
              ) : !fileStructure?.structure ? (
                <Empty 
                  description="暂无文件" 
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  style={{ padding: '40px 20px' }}
                />
              ) : searchQuery ? (
                filteredFiles.length === 0 ? (
                  <Empty 
                    description="未找到匹配的文件" 
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    style={{ padding: '40px 20px' }}
                  />
                ) : (
                  filteredFiles.map(node => (
                    <div
                      key={node.path}
                      className={`tree-node ${node.type} ${selectedFile?.path === node.path ? 'selected' : ''}`}
                      style={{ paddingLeft: '12px' }}
                      onClick={() => {
                        if (node.type === 'file') {
                          handlePreviewFile(node.path, node.name)
                        } else {
                          toggleFolder(node.path)
                        }
                      }}
                    >
                      <span className={`node-icon icon-${node.type}`}>
                        {node.type === 'folder' ? <FolderOutlined /> : <FileTextOutlined />}
                      </span>
                      <span className="node-name">
                        <HighlightText text={node.path} highlight={debouncedSearchQuery} />
                      </span>
                      {node.size !== undefined && (
                        <span className="node-size">{formatFileSize(node.size)}</span>
                      )}
                    </div>
                  ))
                )
              ) : fileStructure.structure.type === 'file' ? (
                // 单个文件的情况
                <div
                  className={`tree-node file ${selectedFile?.path === fileStructure.structure.path ? 'selected' : ''}`}
                  style={{ paddingLeft: '12px' }}
                  onClick={() => handlePreviewFile(fileStructure.structure.path, fileStructure.structure.name)}
                >
                  <span className="node-icon icon-file">
                    <FileTextOutlined />
                  </span>
                  <span className="node-name">{fileStructure.structure.name}</span>
                  {fileStructure.structure.size !== undefined && (
                    <span className="node-size">{formatFileSize(fileStructure.structure.size)}</span>
                  )}
                </div>
              ) : (
                // 目录结构的情况
                fileStructure.structure.children.length > 0 ? (
                  fileStructure.structure.children.map((node) => renderTreeNode(buildFileNode(node, 0), true))
                ) : (
                  <Empty 
                    description="暂无文件" 
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    style={{ padding: '40px 20px' }}
                  />
                )
              )}
            </div>
          </aside>

          {/* 编辑器区域 */}
          <main className="editor-container">
            {/* 面包屑导航 */}
            {selectedFile && (
              <div className="breadcrumb">
                {selectedFile.path.split('/').map((part, index, arr) => (
                  <div className="breadcrumb-item" key={index}>
                    <span>{part}</span>
                    {index < arr.length - 1 && <span className="breadcrumb-separator">›</span>}
                  </div>
                ))}
              </div>
            )}
            
            {/* 代码编辑器 */}
            <div className="code-editor">
              {fileLoading ? (
                <div style={{ 
                  display: 'flex', 
                  justifyContent: 'center', 
                  alignItems: 'center',
                  height: '100%' 
                }}>
                  <Spin tip="加载文件中..." size="large">
                    <div style={{ height: 200 }} />
                  </Spin>
                </div>
              ) : !selectedFile ? (
                <Empty 
                  description="请选择要预览的文件" 
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  style={{ 
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'center',
                    alignItems: 'center',
                    height: '100%'
                  }}
                />
              ) : !selectedFile.data.is_text ? (
                <div style={{ 
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'center',
                  alignItems: 'center',
                  height: '100%',
                  padding: '40px',
                  textAlign: 'center'
                }}>
                  <div style={{ 
                    fontSize: '72px',
                    marginBottom: '24px',
                    opacity: 0.3
                  }}>
                    📄
                  </div>
                  <div style={{ 
                    fontSize: '18px',
                    fontWeight: 600,
                    marginBottom: '12px',
                    color: 'var(--text-primary)'
                  }}>
                    {(selectedFile.data as ProjectFileContent & { file_type_description?: string }).file_type_description || '此文件类型'}不支持在线预览
                  </div>
                  <div style={{ 
                    fontSize: '14px',
                    color: 'var(--text-secondary)',
                    marginBottom: '24px',
                    maxWidth: '400px'
                  }}>
                    该文件是二进制文件或不支持在线预览的文件类型。<br/>
                    您可以下载文件到本地后使用相应的程序打开。
                  </div>
                  <div style={{ display: 'flex', gap: '12px' }}>
                    <Button 
                      type="primary"
                      icon={<DownloadOutlined />}
                      onClick={() => handleDownloadFile(selectedFile.path, selectedFile.name)}
                    >
                      下载文件
                    </Button>
                    <Button 
                      onClick={handleClosePreview}
                    >
                      关闭
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="code-content" style={{ height: '100%', width: '100%' }}>
                  <Editor
                    height="100%"
                    language={getMonacoLanguage(selectedFile.name)}
                    value={selectedFile.data.content || ''}
                    theme={getMonacoTheme(isDark)}
                    onChange={handleEditorChange}
                    onMount={handleEditorDidMount}
                    options={getEditorOptions(false, isDark)}
                    loading={<Spin size="large" />}
                  />
                </div>
              )}
            </div>
            
            {/* 状态栏 */}
            {selectedFile && (
              <div className="status-bar">
                <div className="status-left">
                  <div className="status-item">
                    <span className="status-indicator" style={{
                      backgroundColor: hasUnsavedChanges ? '#faad14' : '#52c41a'
                    }}></span>
                    <span>{hasUnsavedChanges ? '未保存' : '就绪'}</span>
                  </div>
                  <div className="status-item">
                    <span>{language.toUpperCase()}</span>
                  </div>
                  <div className="status-item">
                    <span>{selectedFile.data.encoding || 'UTF-8'}</span>
                  </div>
                </div>
                <div className="status-right">
                  <div className="status-item">
                    <span>{formatFileSize(selectedFile.data.size || 0)}</span>
                  </div>
                  {selectedFile.data.modified_time && (
                    <div className="status-item">
                      <span>{formatTime(selectedFile.data.modified_time)}</span>
                    </div>
                  )}
                </div>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  )
}

export default React.memo(ProjectFileManager)
