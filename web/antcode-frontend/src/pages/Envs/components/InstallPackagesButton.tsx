import type React from 'react'
import { useState } from 'react'
import { Button, Modal, Select } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import envService from '@/services/envs'
import { runtimeService } from '@/services/runtimes'
import type { InstallPackagesButtonProps } from '../types'

const InstallPackagesButton: React.FC<InstallPackagesButtonProps> = ({
  venvId,
  onInstalled,
  batch = false,
  selectedIds = [],
  buttonId,
}) => {
  const [open, setOpen] = useState(false)
  const [pkgs, setPkgs] = useState<string[]>([])
  const [loading, setLoading] = useState(false)

  const submit = async () => {
    if (!pkgs.length) return
    setLoading(true)
    try {
      if (batch) {
        if (!selectedIds.length) {
          Modal.warning({ title: '请先选择要安装依赖的环境' })
          return
        }
        // 批量安装
        for (const id of selectedIds) {
          if (id.includes('|')) {
            const [workerId, envName] = id.split('|')
            await runtimeService.installPackages(workerId, envName, pkgs)
          } else {
            await envService.installPackagesToVenv(id, pkgs)
          }
        }
      } else {
        if (venvId.includes('|')) {
          const [workerId, envName] = venvId.split('|')
          await runtimeService.installPackages(workerId, envName, pkgs)
        } else {
          await envService.installPackagesToVenv(venvId, pkgs)
        }
      }
      setOpen(false)
      setPkgs([])
      if (onInstalled) {
        onInstalled()
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <Button
        size={batch ? 'middle' : 'small'}
        onClick={() => setOpen(true)}
        id={buttonId}
        disabled={batch && !selectedIds.length}
        icon={batch ? <DownloadOutlined /> : undefined}
      >
        {batch ? `批量安装依赖${selectedIds.length > 0 ? ` (${selectedIds.length})` : ''}` : '安装依赖'}
      </Button>
      <Modal
        open={open}
        onCancel={() => setOpen(false)}
        title={batch ? '批量安装依赖' : '安装依赖'}
        onOk={submit}
        confirmLoading={loading}
      >
        <Select
          mode="tags"
          style={{ width: '100%' }}
          placeholder="输入包名后回车，如: requests==2.32.3"
          value={pkgs}
          onChange={(value: string[]) => setPkgs(value)}
        />
      </Modal>
    </>
  )
}

export default InstallPackagesButton
