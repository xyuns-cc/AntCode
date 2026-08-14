import type React from 'react'
import { Button, Card, Col, Form, Input, Row, Select, Space, Tag, Typography } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import type { GitRepository } from '@/types/repository'

const { Text } = Typography
const { TextArea } = Input
const { Option } = Select

interface FileProjectGitFieldsProps {
  repositories: GitRepository[]
}

export const FileProjectGitFields: React.FC<FileProjectGitFieldsProps> = ({ repositories }) => (
  <Card title="Git 来源" size="small" style={{ marginBottom: 16 }}>
    <Form.Item
      name="repository_id"
      label="代码仓库"
      rules={[{ required: true, message: '请选择代码仓库' }]}
    >
      <Select showSearch placeholder="选择已管理仓库" optionFilterProp="label">
        {repositories.map((repository) => (
          <Option key={repository.id} value={repository.id} label={repository.name}>
            <Space direction="vertical" size={0}>
              <span>{repository.name}</span>
              <Text type="secondary" style={{ fontSize: 12 }}>
                {repository.url}
              </Text>
            </Space>
          </Option>
        ))}
      </Select>
    </Form.Item>

    <Row gutter={16}>
      <Col span={12}>
        <Form.Item name="ref" label="Git 引用">
          <Input placeholder="main" />
        </Form.Item>
      </Col>
      <Col span={12}>
        <Form.Item
          name="subdir"
          label="项目子目录"
          rules={[{ required: true, message: '请输入仓库内项目子目录' }]}
        >
          <Input placeholder="spiders/news" />
        </Form.Item>
      </Col>
    </Row>

    <Form.Item name="include_paths" label="共享目录">
      <Input placeholder="libs/common, libs/utils" />
    </Form.Item>
  </Card>
)

interface FileProjectRuntimeFieldsProps {
  dependencies: string[]
  dependencyInput: string
  onDependencyInputChange: (value: string) => void
  onAddDependency: () => void
  onRemoveDependency: (dependency: string) => void
}

export const FileProjectRuntimeFields: React.FC<FileProjectRuntimeFieldsProps> = ({
  dependencies,
  dependencyInput,
  onDependencyInputChange,
  onAddDependency,
  onRemoveDependency,
}) => (
  <Card title="运行配置" size="small" style={{ marginBottom: 16 }}>
    <FileProjectEntryFields />
    <FileProjectJsonFields />
    <FileProjectDependencyFields
      dependencies={dependencies}
      dependencyInput={dependencyInput}
      onDependencyInputChange={onDependencyInputChange}
      onAddDependency={onAddDependency}
      onRemoveDependency={onRemoveDependency}
    />
  </Card>
)

const FileProjectEntryFields: React.FC = () => (
  <Row gutter={16}>
    <Col span={8}>
      <Form.Item
        name="language"
        label="执行语言"
        tooltip="选择项目的执行语言；worker 会自动装配对应依赖并选运行时"
        initialValue="python"
      >
        <Select>
          <Option value="python">Python</Option>
          <Option value="javascript">JavaScript (Node.js)</Option>
          <Option value="typescript">TypeScript</Option>
          <Option value="java">Java</Option>
          <Option value="go">Go</Option>
        </Select>
      </Form.Item>
    </Col>
    <Col span={16}>
      <Form.Item
        name="entry_point"
        label="入口文件"
        tooltip="指定项目的主入口文件；后缀决定运行时（.py/.js/.ts/.jar/.go）"
        rules={[{ required: true, message: '请输入入口文件' }]}
      >
        <Input placeholder="例如: main.py / index.js / app.jar / main.go" />
      </Form.Item>
    </Col>
  </Row>
)

const FileProjectJsonFields: React.FC = () => (
  <Row gutter={16}>
    <Col span={12}>
      <Form.Item name="runtime_config" label="运行时配置" tooltip='JSON格式，如 {"max_workers": 4}'>
        <TextArea rows={3} placeholder='{"max_workers": 4, "timeout": 30}' />
      </Form.Item>
    </Col>
    <Col span={12}>
      <Form.Item
        name="environment_vars"
        label="环境变量"
        tooltip='JSON格式，如 {"API_KEY": "your_key"}'
      >
        <TextArea rows={3} placeholder='{"API_KEY": "your_key", "DEBUG": "true"}' />
      </Form.Item>
    </Col>
  </Row>
)

const FileProjectDependencyFields: React.FC<FileProjectRuntimeFieldsProps> = ({
  dependencies,
  dependencyInput,
  onDependencyInputChange,
  onAddDependency,
  onRemoveDependency,
}) => (
  <Form.Item label="Python 依赖包">
    <Space direction="vertical" style={{ width: '100%' }}>
      <Space.Compact style={{ width: '100%' }}>
        <Input
          placeholder="输入依赖包名，如 requests"
          value={dependencyInput}
          onChange={(event) => onDependencyInputChange(event.target.value)}
          onPressEnter={onAddDependency}
        />
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={onAddDependency}
          disabled={!dependencyInput.trim()}
        >
          添加
        </Button>
      </Space.Compact>

      {dependencies.map((dependency) => (
        <Tag
          key={dependency}
          closable
          onClose={() => onRemoveDependency(dependency)}
          style={{ marginBottom: 4 }}
        >
          {dependency}
        </Tag>
      ))}
    </Space>
  </Form.Item>
)
