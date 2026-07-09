# Worker Capability 声明与路由

T6-T4 让 worker 显式声明能处理的 task type，master 派发时按能力过滤，
避免把 rule 任务派到关掉 `RulePlugin` 的 code-only worker。

## Worker 侧：开关 & 声明

env / config 决定加载哪些插件：

| 变量 | 默认 | 说明 |
|---|---|---|
| `WORKER_ENABLE_RULE_PLUGIN` | `true` | `false` 关掉 RulePlugin |

其他 3 个插件（Code / Spider / Render）无条件尝试加载，失败会 log
warning。真实生效的 task type 列表通过
`PluginRegistry.capabilities()` 拿到。

worker 注册时 `_register_direct_worker` 把 `capabilities.task_types`
上报给 web_api → 落 `workers.capabilities` JSONField：

```json
{
  "curl_cffi": {"enabled": true},
  "task_types": ["code", "spider", "render", "rule"]
}
```

## Master 侧：dispatcher 路由

`WorkerLoadBalancer.select_best_worker(require_task_type=...)`：

- 批分发（`dispatch_batch`）时，如果批内所有任务的 `project_type` 一致
  （通常都是），把 project_type 作为 capability 过滤条件
- worker 未上报 `task_types` 视为兼容任意（老 worker 向后兼容）
- 无匹配 worker 时返回明确错误：`"无支持 task_type='rule' 的 Worker
  (检查 worker 侧插件是否装载)"`

## 部署示例

**混合池**（默认）：所有 worker 都能跑 code + rule + spider + render。
一台 worker 通吃。

**code-only pool**（省磁盘 / 隔离爬虫依赖）：
```env
WORKER_ENABLE_RULE_PLUGIN=false
```
这类 worker 只接 code / file / spider / render 任务；rule 项目被派到
时 dispatcher 会跳过它。

**rule-only pool**（想区分抓取 worker）：Code/Spider/Render 是 workspace
分配 + 沙箱开销问题，目前没做 env 关掉的 gate。真要做可以照
`WORKER_ENABLE_RULE_PLUGIN` 的 pattern 加。

## 老 worker 兼容

老 worker 不上报 `task_types` → dispatcher 认为它兼容任意类型。生产环境
滚动升级期间新老 worker 共存不会派错任务。所有 worker 升级完就自动进入
严格路由态。
