import { Button, Modal, Space, Typography } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'

import type { CrawlBatchSummary } from '@/services/crawl'

const { Text } = Typography

interface CrawlBatchItemsModalProps {
  open: boolean
  batch?: CrawlBatchSummary
  items?: unknown[]
  onClose: () => void
  onDownload: (batch: CrawlBatchSummary, format: 'json' | 'csv') => void
}

const CrawlBatchItemsModal = ({
  open,
  batch,
  items,
  onClose,
  onDownload,
}: CrawlBatchItemsModalProps) => (
  <Modal
    title={`抓取数据 - ${batch?.name || ''}`}
    open={open}
    onCancel={onClose}
    footer={
      batch ? (
        <Space>
          <Button icon={<DownloadOutlined />} onClick={() => onDownload(batch, 'json')}>
            下载 JSON
          </Button>
          <Button icon={<DownloadOutlined />} onClick={() => onDownload(batch, 'csv')}>
            下载 CSV
          </Button>
          <Button onClick={onClose}>关闭</Button>
        </Space>
      ) : null
    }
    width={900}
    destroyOnHidden
  >
    <Text type="secondary" style={{ fontSize: 12 }}>
      展示前 100 条；下载包含批次当前已有的全部抓取数据。
    </Text>
    <pre
      style={{
        maxHeight: 480,
        overflow: 'auto',
        background: 'rgba(0,0,0,0.02)',
        padding: 12,
        marginTop: 8,
      }}
    >
      {JSON.stringify(items || [], null, 2)}
    </pre>
  </Modal>
)

export default CrawlBatchItemsModal
