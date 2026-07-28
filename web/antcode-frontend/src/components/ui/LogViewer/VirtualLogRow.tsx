import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { FC } from 'react'
import styles from './LogViewer.module.css'
import type { LogMessage } from './enhancedLogViewerTypes'
import type { LogDisplayOptions } from './virtualLogTypes'
import { splitHighlightedText } from './virtualLogUtils'

const NEW_ITEM_ANIMATION_MS = 300

interface LogRowProps {
  displayOptions: LogDisplayOptions
  isNew: boolean
  message: LogMessage
  onHeightChange: (id: string, height: number) => void
  onMessageClick?: (message: LogMessage) => void
  searchText: string
  top: number
}

const useEntryAnimation = (isNew: boolean): boolean => {
  const [showAnimation, setShowAnimation] = useState(isNew)
  useEffect(() => {
    if (isNew) setShowAnimation(true)
  }, [isNew])
  useEffect(() => {
    if (!showAnimation) return undefined
    const timer = setTimeout(() => setShowAnimation(false), NEW_ITEM_ANIMATION_MS)
    return () => clearTimeout(timer)
  }, [showAnimation])
  return showAnimation
}

const useRowMeasurement = (
  message: LogMessage,
  details: { onHeightChange: LogRowProps['onHeightChange']; rowRef: React.RefObject<HTMLDivElement> },
) => {
  const lastHeight = useRef(0)
  const reportHeight = useCallback((element: HTMLElement) => {
    const height = element.offsetHeight
    if (height <= 0 || Math.abs(height - lastHeight.current) <= 1) return
    lastHeight.current = height
    details.onHeightChange(message.id, height)
  }, [details, message.id])
  useLayoutEffect(() => {
    if (details.rowRef.current) reportHeight(details.rowRef.current)
  }, [details.rowRef, message.content, reportHeight])
  useEffect(() => {
    const element = details.rowRef.current
    if (!element) return undefined
    const observer = new ResizeObserver((entries) => entries.forEach((entry) => reportHeight(entry.target as HTMLElement)))
    observer.observe(element)
    return () => observer.disconnect()
  }, [details.rowRef, reportHeight])
}

const LogRow: FC<LogRowProps> = (props) => {
  const rowRef = useRef<HTMLDivElement>(null)
  const showAnimation = useEntryAnimation(props.isNew)
  const measurement = useMemo(() => ({ onHeightChange: props.onHeightChange, rowRef }), [props.onHeightChange])
  useRowMeasurement(props.message, measurement)
  const parts = useMemo(
    () => splitHighlightedText(props.message.content, props.searchText),
    [props.message.content, props.searchText],
  )
  const typeClass = styles[props.message.type] || styles.stdout
  const className = `${styles.logLine} ${typeClass}${showAnimation ? ` ${styles.newItem}` : ''}`
  return <div
    ref={rowRef}
    className={className}
    style={{ position: 'absolute', top: props.top, left: 0, right: 0, cursor: props.onMessageClick ? 'pointer' : 'default' }}
    onClick={() => props.onMessageClick?.(props.message)}
    title={`${props.message.type.toUpperCase()} - ${new Date(props.message.timestamp).toLocaleString()}`}
  >
    {props.displayOptions.showTimestamp && <span className={styles.timestamp}>
      [{new Date(props.message.timestamp).toLocaleTimeString()}]
    </span>}
    <span className={styles.logType}>[{props.message.type.toUpperCase()}]</span>
    {props.displayOptions.showLevel && props.message.level && <span className={styles.logLevel}>[{props.message.level}]</span>}
    {props.displayOptions.showSource && props.message.source && <span className={styles.logLevel}>[{props.message.source}]</span>}
    <span className={styles.content}>{parts.map((part, index) => (
      part.highlighted ? <span key={index} className={styles.highlight}>{part.text}</span> : part.text
    ))}</span>
  </div>
}

const sameLogRow = (previous: LogRowProps, next: LogRowProps): boolean => (
  previous.message === next.message
  && previous.top === next.top
  && previous.searchText === next.searchText
  && previous.isNew === next.isNew
  && previous.displayOptions === next.displayOptions
  && previous.onMessageClick === next.onMessageClick
  && previous.onHeightChange === next.onHeightChange
)

export default memo(LogRow, sameLogRow)
