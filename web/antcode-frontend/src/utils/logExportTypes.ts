export type ExportFormat = 'txt' | 'json' | 'csv'

export interface LogExportConfig {
  runId: string
  format: ExportFormat
  includeStdout?: boolean
  includeStderr?: boolean
  includeTimestamp?: boolean
  includeLevel?: boolean
  includeSource?: boolean
  maxLines?: number
}
