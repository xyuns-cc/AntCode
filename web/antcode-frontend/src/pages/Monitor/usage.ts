import type { WorkerDisplayData } from './types'

/**
 * 一台机器某一项资源使用率的「读数」。
 *
 * transformWorker 把没上报过指标的机器压成 cpu/memory/disk = 0（阈值判定和条形图都要数字），
 * 于是「从没上报过」和「真的很闲」在渲染层长得一模一样：一台刚接进来、一次心跳都没有的机器
 * 会被画成三根 0% 的彩色进度条。集群均值和磁盘图已经靠 hasMetrics 把这类机器排除掉了
 * （见 charts/data.ts），Worker 卡片 / 抽屉 / 详情三处仍在照单全画。
 */

/** 没有读数时的占位。「没上报过」必须和「占用 0%」长得不一样。 */
export const NO_READING = '—'

/** 缺读数时的条形颜色，与 charts/data.ts 的占位柱同一个中性灰。 */
const NO_READING_COLOR = '#d9d9d9'

const HIGH_USAGE = 80
const ELEVATED_USAGE = 60

/** null = 这台机器从没上报过指标，不是「占用 0%」。 */
export const usageReading = (
  worker: WorkerDisplayData,
  field: 'cpu' | 'memory' | 'disk',
): number | null => (worker.hasMetrics ? worker[field] : null)

export const usageText = (value: number | null): string =>
  value === null ? NO_READING : `${Math.round(value)}%`

/** 没读数时不涂业务色：一条彩色的空条读起来就是「占用很低」。 */
export const usageBarColor = (value: number | null, normal: string): string => {
  if (value === null) return NO_READING_COLOR
  if (value > HIGH_USAGE) return '#ff4d4f'
  return value > ELEVATED_USAGE ? '#faad14' : normal
}
