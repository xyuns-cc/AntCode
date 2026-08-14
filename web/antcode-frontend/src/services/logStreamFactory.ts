import type { LogStreamConnection, LogStreamOptions } from './logs'
import { EventSourceLogConnection } from './logStreamConnection'

export const createLogStreamConnection = (
  getTicket: () => Promise<string>,
  options: LogStreamOptions,
): LogStreamConnection => {
  const connection = new EventSourceLogConnection(getTicket, options)
  connection.start()
  return connection
}
