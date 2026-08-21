/**
 * Worker 自报快照读不回来时的展示视图。
 *
 * 后端对读不回来的列返回 metrics/capabilities = null 并附 snapshotErrors（见
 * worker_snapshot_readback.py）。这里必须与"这台机器没报过指标"区分开：两者都是
 * null，但前者是控制面 schema 与 Worker 二进制键集错配，需要有人去补 schema；
 * 后者只是还没心跳。表格里都渲染成 '—' 的话，前者会被当成"新接入的机器"忽略掉。
 */

import type { WorkerSnapshotError } from '@/types/worker'

export const SNAPSHOT_ERROR_LABEL = '读取失败'
export const METRICS_COLUMN = 'metrics'
export const CAPABILITIES_COLUMN = 'capabilities'

/** 该列是否读回失败；仅凭 metrics == null 判不出来，必须看 snapshotErrors。 */
export function snapshotErrorFor(
  errors: WorkerSnapshotError[] | undefined,
  column: string
): WorkerSnapshotError | undefined {
  return errors?.find((error) => error.column === column)
}

/** tooltip 文案带上键名，否则运维只知道"坏了"不知道坏在哪个键。 */
export function snapshotErrorTooltip(error: WorkerSnapshotError): string {
  return `${error.column} 读取失败（控制面 schema 未声明或取值越界）: ${error.message}`
}
