import { useNavigate } from 'react-router'
import './charts/setup'
import { AlertsDrawer } from './drawers/AlertsDrawer'
import { WorkerDetailDrawer } from './drawers/WorkerDetailDrawer'
import { WorkersDrawer } from './drawers/WorkersDrawer'
import { useMonitorController } from './hooks/useMonitorController'
import { AlertsPerformanceSection } from './sections/AlertsPerformanceSection'
import { HeaderStats } from './sections/HeaderStats'
import { TasksSection } from './sections/TasksSection'
import { WorkersSection } from './sections/WorkersSection'
import './monitor.css'

const Monitor = () => {
  const monitor = useMonitorController()
  const navigate = useNavigate()

  return (
    <div className="monitor-container">
      <HeaderStats currentTime={monitor.currentTime} loading={monitor.loading} stats={monitor.view.stats} onRefresh={monitor.handleRefresh} />
      <div style={{ marginTop: 0 }}>
        <WorkersSection
          workers={monitor.workers}
          loading={monitor.loading}
          lastChecked={monitor.lastChecked}
          onShowAll={() => monitor.setShowAllWorkers(true)}
          onSelect={monitor.setSelectedWorker}
        />
        <AlertsPerformanceSection
          alerts={monitor.view.alerts}
          period={monitor.performancePeriod}
          cpuData={monitor.view.cpuData}
          memoryData={monitor.view.memoryData}
          chartOptions={monitor.view.chartOptions}
          onShowAllAlerts={() => monitor.setShowAllAlerts(true)}
          onPeriodChange={monitor.setPerformancePeriod}
        />
        {/* 监控页的任务表只有名称/Worker/状态三列，「详情」不在这里就地重造一份，而是跳到
            已有的任务详情页（App.tsx 的 tasks/:id）；MonitorTask.id 就是 TaskResponse.id，
            与 /tasks 列表页的「查看」跳的是同一条路由。 */}
        <TasksSection
          tasks={monitor.tasks}
          onViewTask={(taskId) => navigate(`/tasks/${taskId}`)}
          taskStatsData={monitor.view.taskStatsData}
          diskUsageData={monitor.view.diskUsageData}
          taskBarOptions={monitor.view.taskBarOptions}
          diskBarOptions={monitor.view.diskBarOptions}
        />
      </div>
      <WorkersDrawer
        open={monitor.showAllWorkers}
        workers={monitor.workers}
        onClose={() => monitor.setShowAllWorkers(false)}
        onSelect={monitor.setSelectedWorker}
      />
      <AlertsDrawer open={monitor.showAllAlerts} alerts={monitor.view.alerts} onClose={() => monitor.setShowAllAlerts(false)} />
      <WorkerDetailDrawer
        worker={monitor.selectedWorker}
        tasks={monitor.tasks}
        chartRef={monitor.chartRef}
        chartData={monitor.view.workerData}
        chartOptions={monitor.view.workerOptions}
        onClose={() => monitor.setSelectedWorker(null)}
      />
    </div>
  )
}

export default Monitor
