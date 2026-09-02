import { useCallback, useState } from 'react'
import type { ClusterSpiderStats } from '@/types'

/**
 * 「最近完成请求量」「响应延迟」两张趋势图的本地采样序列。
 *
 * 后端只给当前快照，这两张图是前端把每一轮轮询的快照攒起来画出来的。这条职责独有的失效模式
 * 是给**没取到的那一轮**补一个 0 采样：趋势线会画出一次并不存在的跌零，而那恰恰是后端挂掉的
 * 时刻——图上看起来像「流量真的停了」。所以只有拿到快照才追加，失败时序列原地不动。
 * 宿主 SpiderStatsTab 的失效模式是另一回事（拉取分路、轮询节拍、图表编排）。
 */

const SAMPLE_LIMIT = 20

export interface SpiderTrendSample {
  time: string
  reqRate: number
  latency: number
}

export const useSpiderTrendSamples = () => {
  const [samples, setSamples] = useState<SpiderTrendSample[]>([])
  const append = useCallback((snapshot: ClusterSpiderStats) => {
    const time = new Date().toLocaleTimeString('zh-CN', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
    })
    setSamples((previous) => [
      ...previous,
      { time, reqRate: snapshot.clusterRequestsPerMinute, latency: snapshot.avgLatencyMs },
    ].slice(-SAMPLE_LIMIT))
  }, [])
  return { samples, append }
}
