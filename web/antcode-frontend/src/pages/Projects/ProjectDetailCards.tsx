import type React from 'react'
import { Card, Descriptions, Tag, Typography, Collapse, Tooltip } from 'antd'
import ResponsiveTable from '@/components/common/ResponsiveTable'
import CopyableTooltip from '@/components/common/CopyableTooltip'
import type { Project } from '@/types'

const { Text, Paragraph } = Typography
const { Panel } = Collapse

type CodeBlockStyle = React.CSSProperties

interface DetailCardProps {
  project: Project
  codeBlockStyle: CodeBlockStyle
}

interface ExtractionRule {
  page_type?: string
  desc?: string
  type?: string
  expr?: string
}

const renderJsonBlock = (title: string, value: unknown, style: CodeBlockStyle) => (
  <div style={{ marginTop: 16 }}>
    <Text strong>{title}:</Text>
    <Paragraph>
      <pre style={style}>{JSON.stringify(value, null, 2)}</pre>
    </Paragraph>
  </div>
)

const renderRepositoryDescriptions = (info: {
  entry_point?: string
  resolved_revision?: string
  repository_name?: string
  repository_url?: string
  ref?: string
  subdir?: string
  include_paths?: string[]
}) => (
  <>
    <Descriptions.Item label="入口文件">{info.entry_point || '未指定'}</Descriptions.Item>
    <Descriptions.Item label="Resolved Revision">
      <Text code>{info.resolved_revision || '-'}</Text>
    </Descriptions.Item>
    <Descriptions.Item label="代码仓库">{info.repository_name || '-'}</Descriptions.Item>
    <Descriptions.Item label="仓库地址" span={2}>
      <Text code style={{ wordBreak: 'break-all' }}>{info.repository_url || '-'}</Text>
    </Descriptions.Item>
    <Descriptions.Item label="Git 引用">{info.ref || '-'}</Descriptions.Item>
    <Descriptions.Item label="项目子目录">
      <Text code>{info.subdir || '-'}</Text>
    </Descriptions.Item>
    <Descriptions.Item label="共享目录" span={2}>
      {(info.include_paths || []).join(', ') || '-'}
    </Descriptions.Item>
  </>
)

export const FileInfoCard: React.FC<DetailCardProps> = ({ project, codeBlockStyle }) => {
  if (!project.file_info) return null

  return (
    <Card title="文件项目详情" style={{ marginTop: 16 }}>
      <Descriptions column={2} bordered>
        {renderRepositoryDescriptions(project.file_info)}
      </Descriptions>
      {project.file_info.runtime_config &&
        renderJsonBlock('运行时配置', project.file_info.runtime_config, codeBlockStyle)}
      {project.file_info.environment_vars &&
        renderJsonBlock('环境变量', project.file_info.environment_vars, codeBlockStyle)}
    </Card>
  )
}

const buildRuleColumns = () => [
  {
    title: '规则描述',
    dataIndex: 'desc',
    key: 'desc',
    width: 150,
    ellipsis: { showTitle: false },
    render: (desc: string) => (
      <Tooltip title={desc} placement="topLeft">
        <span>{desc}</span>
      </Tooltip>
    ),
  },
  {
    title: '规则类型',
    dataIndex: 'type',
    key: 'type',
    width: 100,
    render: (type: string) => <Tag color="blue">{type.toUpperCase()}</Tag>,
  },
  {
    title: '选择器表达式',
    dataIndex: 'expr',
    key: 'expr',
    ellipsis: { showTitle: false },
    render: (expr: string) => (
      <Tooltip title={expr} placement="topLeft">
        <Text code style={{ wordBreak: 'break-all' }}>{expr}</Text>
      </Tooltip>
    ),
  },
]

const RuleTable: React.FC<{ rules: ExtractionRule[] }> = ({ rules }) => (
  <ResponsiveTable
    columns={buildRuleColumns()}
    dataSource={rules}
    rowKey={(record, idx) => `${record.type}-${record.expr}-${idx}`}
    pagination={false}
    size="small"
  />
)

export const RuleInfoCard: React.FC<DetailCardProps> = ({ project, codeBlockStyle }) => {
  if (!project.rule_info) return null

  const extractionRules: ExtractionRule[] = Array.isArray(project.rule_info.extraction_rules)
    ? project.rule_info.extraction_rules
    : []
  const listRules = extractionRules.filter((rule) => rule.page_type === 'list')
  const detailRules = extractionRules.filter((rule) => rule.page_type === 'detail')

  return (
    <Card title="规则项目详情" style={{ marginTop: 16 }}>
      <Descriptions column={2} bordered>
        <Descriptions.Item label="采集引擎">
          <Tag color="green">{project.rule_info.engine}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="目标URL">
          <CopyableTooltip text={project.rule_info.target_url}>
            <span style={{ cursor: 'pointer' }}>{project.rule_info.target_url}</span>
          </CopyableTooltip>
        </Descriptions.Item>
        <Descriptions.Item label="回调类型">
          <Tag color="blue">{project.rule_info.callback_type}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="请求方法">{project.rule_info.request_method}</Descriptions.Item>
        <Descriptions.Item label="请求间隔">{project.rule_info.request_delay}ms</Descriptions.Item>
        <Descriptions.Item label="最大页数">{project.rule_info.max_pages}</Descriptions.Item>
        {/* S10: Scrapy 引擎相关能力开关 */}
        <Descriptions.Item label="断点续爬">
          <Tag color={project.rule_info.resume_enabled ? 'purple' : 'default'}>
            {project.rule_info.resume_enabled ? '已启用' : '未启用'}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="内容去重">
          {(() => {
            const cfg = project.rule_info.dedup_config
            if (!cfg || !cfg.enabled) return <Tag>未启用</Tag>
            return (
              <span>
                <Tag color="cyan">已启用</Tag>
                <span style={{ fontSize: 12 }}>
                  {`scope=${cfg.scope || 'project'}, fields=[${(cfg.fields || []).join(', ')}]`}
                </span>
              </span>
            )
          })()}
        </Descriptions.Item>
      </Descriptions>

      <Collapse style={{ marginTop: 16 }}>
        {project.rule_info.callback_type === 'mixed' ? (
          <>
            <Panel header={`列表页规则 (${listRules.length})`} key="list">
              <RuleTable rules={listRules} />
            </Panel>
            <Panel header={`详情页规则 (${detailRules.length})`} key="detail">
              <RuleTable rules={detailRules} />
            </Panel>
          </>
        ) : (
          <Panel header={`提取规则 (${extractionRules.length})`} key="all">
            <RuleTable rules={extractionRules} />
          </Panel>
        )}
        {project.rule_info.headers && (
          <Panel header="请求头配置" key="headers">
            <pre style={codeBlockStyle}>{JSON.stringify(project.rule_info.headers, null, 2)}</pre>
          </Panel>
        )}
        {project.rule_info.pagination_config && (
          <Panel header="分页配置" key="pagination">
            <pre style={codeBlockStyle}>
              {JSON.stringify(project.rule_info.pagination_config, null, 2)}
            </pre>
          </Panel>
        )}
      </Collapse>
    </Card>
  )
}

export const CodeInfoCard: React.FC<DetailCardProps> = ({ project, codeBlockStyle }) => {
  if (!project.code_info) return null

  return (
    <Card title="代码项目详情" style={{ marginTop: 16 }}>
      <Descriptions column={2} bordered>
        <Descriptions.Item label="编程语言">
          <Tag color="blue">{project.code_info.language}</Tag>
        </Descriptions.Item>
        {renderRepositoryDescriptions(project.code_info)}
      </Descriptions>
      {project.code_info.runtime_config &&
        renderJsonBlock('运行时配置', project.code_info.runtime_config, codeBlockStyle)}
      {project.code_info.environment_vars &&
        renderJsonBlock('环境变量', project.code_info.environment_vars, codeBlockStyle)}
    </Card>
  )
}

export const RuntimeInfoCard: React.FC<{ project: Project }> = ({ project }) => (
  <Card title="运行环境" style={{ marginTop: 16 }}>
    <Descriptions column={1} bordered>
      <Descriptions.Item label="位置">{project.env_location || 'worker'}</Descriptions.Item>
      <Descriptions.Item label="Worker">{project.worker_id || '-'}</Descriptions.Item>
      <Descriptions.Item label="环境名称">{project.worker_env_name || '-'}</Descriptions.Item>
      <Descriptions.Item label="作用域">{project.runtime_scope || '-'}</Descriptions.Item>
      <Descriptions.Item label="Python版本">{project.python_version || '-'}</Descriptions.Item>
    </Descriptions>
  </Card>
)
