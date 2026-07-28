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
        <TasksSection
          tasks={monitor.tasks}
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
        logs={monitor.view.workerLogs}
        chartRef={monitor.chartRef}
        chartData={monitor.view.workerData}
        chartOptions={monitor.view.workerOptions}
        onClose={() => monitor.setSelectedWorker(null)}
      />
    </div>
  )
}

export default Monitor
