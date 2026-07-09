import type React from 'react'
import { Button, Empty, List, Modal, Popconfirm, Space, Spin, Tag, Typography } from 'antd'
import type { PackageInfo, PackageModalState } from '../types'

const { Text } = Typography

interface PackageListModalProps {
  state: PackageModalState
  onClose: () => void
  onUninstall: (pkg: PackageInfo) => void
}

const PackageListModal: React.FC<PackageListModalProps> = ({ state, onClose, onUninstall }) => (
  <Modal
    open={state.open}
    onCancel={onClose}
    title={`依赖列表 - ${state.env?.key || state.env?.version || ''}`}
    footer={[
      <Button key="close" onClick={onClose}>
        关闭
      </Button>,
    ]}
    width={700}
  >
    <Spin spinning={state.loading || false}>
      {(state.packages || []).length > 0 ? (
        <List
          dataSource={state.packages || []}
          renderItem={(item) => (
            <List.Item
              actions={[
                <Popconfirm
                  key="uninstall"
                  title="确定要卸载此包吗？"
                  onConfirm={() => onUninstall(item)}
                  okText="确定"
                  cancelText="取消"
                >
                  <Button type="link" size="small" danger>
                    卸载
                  </Button>
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    <Text strong>{item.name}</Text>
                    <Tag>{item.version}</Tag>
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      ) : (
        <Empty description="暂无依赖包" />
      )}
    </Spin>
  </Modal>
)

export default PackageListModal
