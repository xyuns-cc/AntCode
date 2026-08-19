/**
 * 批量操作结果的逐项失败原因。
 *
 * 后端三个批量入口（tasks/batch-delete、tasks/batch、projects/batch-delete）
 * 统一在 data.failures 里给出 { id, reason }。reason 已是可读中文（守卫拒绝
 * 原文，或未分类异常的固定文案），前端原样展示即可，不要在这里二次翻译——
 * 那会让"仍由在线 Worker X 持有"这类关键线索被改写成泛化提示。
 */
export interface BatchFailure {
  id: string
  reason: string
}

/** 逐项原因拼成一段可读文本；无失败项时返回空串。 */
export function describeBatchFailures(failures: BatchFailure[]): string {
  return failures.map((failure) => `${failure.id}：${failure.reason}`).join('；')
}
