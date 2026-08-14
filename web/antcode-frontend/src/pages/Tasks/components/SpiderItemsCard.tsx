import { Alert, Button, Card, Empty, List, Space, Tag, Typography, theme } from 'antd'
import { FileOutlined, ReloadOutlined } from '@ant-design/icons'
import type { SpiderItem } from '@/services/runs'
import type { TaskStatus } from '@/types'
import { useSpiderItems } from '../hooks/useSpiderItems'

const DISPLAY_PAGE_SIZE = 20
const { Text } = Typography

interface SpiderItemsCardProps {
  runId: string
  status?: TaskStatus
}

export const SpiderItemsCard = ({ runId, status }: SpiderItemsCardProps) => {
  const { token } = theme.useToken()
  const state = useSpiderItems(runId, status)

  return (
    <Card
      title={
        <Space>
          <FileOutlined />
          <span>抓取数据</span>
          {state.items.length > 0 && <Tag color="green">{state.items.length}</Tag>}
        </Space>
      }
      style={{ marginTop: 16 }}
      extra={
        <Button
          size="small"
          icon={<ReloadOutlined />}
          onClick={state.refresh}
          loading={state.loading}
        >
          刷新
        </Button>
      }
    >
      <SpiderItemResults {...state} background={token.colorBgLayout} />
    </Card>
  )
}

const ItemTitle = ({ item, index }: { item: SpiderItem; index: number }) => (
  <Space>
    <Text code style={{ fontSize: 12 }}>
      #{index + 1}
    </Text>
    {item.spider_name && <Tag color="blue">{item.spider_name}</Tag>}
    {item._id && (
      <Text type="secondary" style={{ fontSize: 11 }}>
        {item._id}
      </Text>
    )}
  </Space>
)

interface SpiderItemResultsProps {
  items: SpiderItem[]
  hasMore: boolean
  loading: boolean
  error: string | null
  loadMore: () => void
  background: string
}

const SpiderItemResults = (props: SpiderItemResultsProps) => {
  if (props.items.length === 0) {
    if (props.error)
      return <Alert type="error" showIcon message="抓取数据加载失败" description={props.error} />
    return (
      <Empty
        description={props.loading ? '加载中...' : '该执行没有抓取数据'}
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
    )
  }
  return (
    <>
      {props.error && (
        <Alert
          type="error"
          showIcon
          message="抓取数据加载失败"
          description={props.error}
          style={{ marginBottom: 12 }}
        />
      )}
      <List
        size="small"
        itemLayout="vertical"
        dataSource={props.items}
        pagination={
          props.items.length > DISPLAY_PAGE_SIZE
            ? { pageSize: DISPLAY_PAGE_SIZE, size: 'small', showSizeChanger: false }
            : false
        }
        renderItem={(item, index) => (
          <SpiderItemRow item={item} index={index} background={props.background} />
        )}
      />
      {props.hasMore && (
        <Button block onClick={props.loadMore} loading={props.loading}>
          加载更多
        </Button>
      )}
    </>
  )
}

const SpiderItemRow = ({
  item,
  index,
  background,
}: {
  item: SpiderItem
  index: number
  background: string
}) => (
  <List.Item key={item._id || index}>
    <List.Item.Meta title={<ItemTitle item={item} index={index} />} />
    <pre
      style={{
        background,
        padding: 8,
        borderRadius: 4,
        fontSize: 12,
        maxHeight: 200,
        overflow: 'auto',
        margin: 0,
      }}
    >
      {typeof item.data === 'string' ? item.data : JSON.stringify(item.data ?? {}, null, 2)}
    </pre>
  </List.Item>
)
