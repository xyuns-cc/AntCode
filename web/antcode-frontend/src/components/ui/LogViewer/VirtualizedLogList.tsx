import { forwardRef, useEffect, useImperativeHandle, useMemo } from 'react'
import VirtualLogRow from './VirtualLogRow'
import type { VirtualizedListProps, VirtualizedListRef } from './virtualLogTypes'
import { useVirtualizedList } from './useVirtualizedList'

const VirtualizedLogList = forwardRef<VirtualizedListRef, VirtualizedListProps>((props, ref) => {
  const model = useVirtualizedList(props)
  useImperativeHandle(ref, () => ({
    resetVirtualization: model.actions.resetVirtualization,
    scrollToBottom: model.actions.scrollToBottom,
  }), [model.actions.resetVirtualization, model.actions.scrollToBottom])
  const range = model.positions.visibleRange
  const visibleItems = props.items.slice(range.start, range.end + 1)
  const newIds = useMemo(() => visibleItems
    .filter((item) => !model.refs.renderedIds.current.has(item.id))
    .map((item) => item.id), [model.refs.renderedIds, visibleItems])
  useEffect(() => {
    newIds.forEach((id) => model.refs.renderedIds.current.add(id))
  }, [model.refs.renderedIds, newIds])
  return <div
    ref={model.refs.container}
    style={{ height: props.height, overflow: 'auto', width: '100%' }}
    onScroll={model.handleScroll}
  >
    <div style={{ height: model.state.totalHeight + 60, position: 'relative', paddingBottom: 60 }}>
      {visibleItems.map((message, index) => {
        const actualIndex = range.start + index
        return <VirtualLogRow
          key={message.id}
          displayOptions={props.displayOptions}
          isNew={newIds.includes(message.id)}
          message={message}
          onHeightChange={model.positions.updateItemHeight}
          onMessageClick={props.onMessageClick}
          searchText={props.searchText}
          top={model.refs.positions.current[actualIndex] ?? 0}
        />
      })}
    </div>
  </div>
})

VirtualizedLogList.displayName = 'VirtualizedLogList'

export default VirtualizedLogList
