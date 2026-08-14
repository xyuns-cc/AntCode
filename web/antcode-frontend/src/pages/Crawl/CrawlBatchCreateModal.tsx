import { Form, Input, InputNumber, Modal, Space } from 'antd'
import type { FormInstance } from 'antd'

interface CrawlBatchCreateModalProps {
  open: boolean
  form: FormInstance
  onClose: () => void
  onSubmit: () => void
}

const CrawlBatchCreateModal = ({ open, form, onClose, onSubmit }: CrawlBatchCreateModalProps) => (
  <Modal
    title="新建爬取批次"
    open={open}
    onCancel={onClose}
    onOk={onSubmit}
    okText="创建"
    cancelText="取消"
    destroyOnHidden
  >
    <Form form={form} layout="vertical" autoComplete="off">
      <Form.Item label="项目 ID" name="project_id" rules={[{ required: true, message: '必填' }]}>
        <Input placeholder="项目 public_id" />
      </Form.Item>
      <Form.Item label="批次名称" name="name" rules={[{ required: true, message: '必填' }]}>
        <Input placeholder="例如 电商-每日-01" />
      </Form.Item>
      <Form.Item
        label="种子 URL（一行一个）"
        name="seed_urls"
        rules={[{ required: true, message: '至少一个' }]}
      >
        <Input.TextArea
          rows={4}
          placeholder="https://example.com/list/1\nhttps://example.com/list/2"
        />
      </Form.Item>
      <Form.Item label="描述（可选）" name="description">
        <Input.TextArea rows={2} />
      </Form.Item>
      <Space>
        <Form.Item label="最大深度" name="max_depth" initialValue={1}>
          <InputNumber min={1} max={10} />
        </Form.Item>
        <Form.Item label="最大页数" name="max_pages" initialValue={100}>
          <InputNumber min={1} max={100000} />
        </Form.Item>
        <Form.Item label="并发" name="max_concurrency" initialValue={4}>
          <InputNumber min={1} max={64} />
        </Form.Item>
      </Space>
    </Form>
  </Modal>
)

export default CrawlBatchCreateModal
