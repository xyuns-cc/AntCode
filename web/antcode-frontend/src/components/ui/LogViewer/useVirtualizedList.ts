import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Dispatch, MutableRefObject, SetStateAction, UIEvent } from 'react'
import type { LogMessage } from './enhancedLogViewerTypes'
import type { VirtualizedListProps } from './virtualLogTypes'
import {
  calculateItemPositions,
  findVisibleRange,
  positionsChanged,
  shouldResetVirtualization,
} from './virtualLogUtils'

const INITIAL_LAYOUT_DELAY_MS = 50
const USER_SCROLL_IDLE_MS = 300
const AUTO_SCROLL_DISTANCE_PX = 50
const SCROLL_RENDER_THRESHOLD_PX = 100
const SCROLL_DIRECTION_THRESHOLD_PX = 10
type Ref<T> = MutableRefObject<T>

interface VirtualRefs {
  autoScrollFrame: Ref<number | null>
  container: Ref<HTMLDivElement | null>
  heights: Ref<Record<string, number>>
  lastItemsLength: Ref<number>
  lastScrollTop: Ref<number>
  layoutFrame: Ref<number | null>
  pendingHeightUpdate: Ref<boolean>
  positions: Ref<number[]>
  previousItemsLength: Ref<number>
  renderedIds: Ref<Set<string>>
  userScrollTimer: Ref<ReturnType<typeof setTimeout> | null>
  userScrolling: Ref<boolean>
}

export interface VirtualState {
  scrollTop: number
  setAutoScroll: (value: boolean) => void
  setScrollTop: Dispatch<SetStateAction<number>>
  setLayoutVersion: Dispatch<SetStateAction<number>>
  setTotalHeight: Dispatch<SetStateAction<number>>
  shouldAutoScroll: boolean
  totalHeight: number
}

type ExternalAutoScroll = Pick<VirtualizedListProps, 'autoScroll' | 'onAutoScrollChange'>

const useVirtualRefs = (initialLength: number): VirtualRefs => {
  const autoScrollFrame = useRef<number | null>(null)
  const container = useRef<HTMLDivElement | null>(null)
  const heights = useRef<Record<string, number>>({})
  const lastItemsLength = useRef(initialLength)
  const lastScrollTop = useRef(0)
  const layoutFrame = useRef<number | null>(null)
  const pendingHeightUpdate = useRef(false)
  const positions = useRef<number[]>([])
  const previousItemsLength = useRef(initialLength)
  const renderedIds = useRef(new Set<string>())
  const userScrollTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const userScrolling = useRef(false)
  return useMemo(() => ({
    autoScrollFrame, container, heights, lastItemsLength, lastScrollTop, layoutFrame,
    pendingHeightUpdate, positions, previousItemsLength, renderedIds, userScrollTimer, userScrolling,
  }), [])
}

// 外部提供 autoScroll 时为受控模式：内部滚动逻辑的开关变化通过 onAutoScrollChange 回传。
const useVirtualState = ({ autoScroll, onAutoScrollChange }: ExternalAutoScroll): VirtualState => {
  const [scrollTop, setScrollTop] = useState(0)
  const [internalAutoScroll, setInternalAutoScroll] = useState(true)
  const [totalHeight, setTotalHeight] = useState(0)
  const [, setLayoutVersion] = useState(0)
  const setAutoScroll = useCallback((value: boolean) => {
    setInternalAutoScroll(value)
    onAutoScrollChange?.(value)
  }, [onAutoScrollChange])
  const shouldAutoScroll = autoScroll ?? internalAutoScroll
  return { scrollTop, setAutoScroll, setLayoutVersion, setScrollTop, setTotalHeight, shouldAutoScroll, totalHeight }
}

interface PositionOptions {
  estimatedItemHeight: number
  height: number
  items: LogMessage[]
  refs: VirtualRefs
  state: VirtualState
}

const usePositionModel = (options: PositionOptions) => {
  const { estimatedItemHeight, height, items, refs, state } = options
  const { scrollTop, setLayoutVersion, setTotalHeight } = state
  const calculatePositions = useCallback(() => {
    const layout = calculateItemPositions(items, {
      heights: refs.heights.current,
      estimatedHeight: estimatedItemHeight,
    })
    const changed = positionsChanged(refs.positions.current, layout.positions)
    refs.positions.current = layout.positions
    if (changed) setLayoutVersion((version) => version + 1)
    setTotalHeight((previous) => (
      Math.abs(previous - layout.totalHeight) > 0.5 || changed ? layout.totalHeight : previous
    ))
  }, [estimatedItemHeight, items, refs, setLayoutVersion, setTotalHeight])
  const updateItemHeight = useCallback((id: string, height: number) => {
    const current = refs.heights.current[id] ?? 0
    if (!id || height <= 0 || Math.abs(current - height) <= 1) return
    refs.heights.current[id] = height
    refs.pendingHeightUpdate.current = true
    if (refs.userScrolling.current || refs.layoutFrame.current !== null) return
    refs.layoutFrame.current = requestAnimationFrame(() => {
      refs.layoutFrame.current = null
      if (refs.userScrolling.current || !refs.pendingHeightUpdate.current) return
      refs.pendingHeightUpdate.current = false
      calculatePositions()
    })
  }, [calculatePositions, refs])
  const itemHeights = items.map((item) => refs.heights.current[item.id] || estimatedItemHeight)
  const visibleRange = findVisibleRange(refs.positions.current, {
    heights: itemHeights, scrollTop, viewportHeight: height,
  })
  return { calculatePositions, updateItemHeight, visibleRange }
}

interface ScrollOptions {
  calculatePositions: () => void
  refs: VirtualRefs
  state: VirtualState
}

const useScrollHandler = ({ calculatePositions, refs, state }: ScrollOptions) => {
  const { scrollTop, setAutoScroll, setScrollTop } = state
  return useCallback((event: UIEvent<HTMLDivElement>) => {
    const container = event.currentTarget
    const nextTop = container.scrollTop
    const atBottom = container.scrollHeight - nextTop - container.clientHeight < AUTO_SCROLL_DISTANCE_PX
    refs.userScrolling.current = true
    if (refs.userScrollTimer.current) clearTimeout(refs.userScrollTimer.current)
    refs.userScrollTimer.current = setTimeout(() => {
      refs.userScrolling.current = false
      setScrollTop(refs.container.current?.scrollTop ?? 0)
      if (!refs.pendingHeightUpdate.current) return
      refs.pendingHeightUpdate.current = false
      calculatePositions()
    }, USER_SCROLL_IDLE_MS)
    if (nextTop < refs.lastScrollTop.current - SCROLL_DIRECTION_THRESHOLD_PX) setAutoScroll(false)
    else if (atBottom) setAutoScroll(true)
    refs.lastScrollTop.current = nextTop
    if (Math.abs(nextTop - scrollTop) > SCROLL_RENDER_THRESHOLD_PX) setScrollTop(nextTop)
  }, [calculatePositions, refs, scrollTop, setAutoScroll, setScrollTop])
}

const useScrollActions = ({ calculatePositions, refs, state }: ScrollOptions) => {
  const { setAutoScroll, setScrollTop } = state
  const scrollToBottom = useCallback(() => {
    setAutoScroll(true)
    refs.userScrolling.current = false
    if (refs.userScrollTimer.current) clearTimeout(refs.userScrollTimer.current)
    refs.userScrollTimer.current = null
    if (refs.autoScrollFrame.current !== null) cancelAnimationFrame(refs.autoScrollFrame.current)
    refs.autoScrollFrame.current = requestAnimationFrame(() => {
      refs.autoScrollFrame.current = null
      if (refs.container.current) refs.container.current.scrollTop = refs.container.current.scrollHeight
    })
  }, [refs, setAutoScroll])
  const resetVirtualization = useCallback(() => {
    refs.heights.current = {}
    refs.positions.current = []
    refs.renderedIds.current = new Set()
    refs.userScrolling.current = false
    setScrollTop(0)
    setAutoScroll(true)
    if (refs.userScrollTimer.current) clearTimeout(refs.userScrollTimer.current)
    refs.userScrollTimer.current = null
    if (refs.container.current) refs.container.current.scrollTop = 0
    calculatePositions()
  }, [calculatePositions, refs, setAutoScroll, setScrollTop])
  return { resetVirtualization, scrollToBottom }
}

const useLayoutEffects = (items: LogMessage[], model: ScrollOptions) => {
  const { calculatePositions, refs, state } = model
  const { setAutoScroll, setScrollTop } = state
  useEffect(() => {
    const timer = setTimeout(calculatePositions, INITIAL_LAYOUT_DELAY_MS)
    return () => clearTimeout(timer)
  }, [calculatePositions])
  useEffect(() => {
    const ids = new Set(items.map((item) => item.id))
    Object.keys(refs.heights.current).forEach((id) => {
      if (!ids.has(id)) delete refs.heights.current[id]
    })
    // renderedIds 随缓冲区裁剪同步清理，长期开页内存不再随历史总量增长。
    refs.renderedIds.current.forEach((id) => {
      if (!ids.has(id)) refs.renderedIds.current.delete(id)
    })
    if (shouldResetVirtualization(items.length, refs.lastItemsLength.current)) {
      refs.heights.current = {}
      refs.positions.current = []
      refs.renderedIds.current = new Set()
      setScrollTop(0)
      setAutoScroll(true)
      if (refs.container.current) refs.container.current.scrollTop = 0
    }
    calculatePositions()
    refs.lastItemsLength.current = items.length
  }, [calculatePositions, items, refs, setAutoScroll, setScrollTop])
}

const useAutoScroll = (itemsLength: number, refs: VirtualRefs, state: VirtualState) => {
  const { shouldAutoScroll } = state
  const previousAutoScroll = useRef(shouldAutoScroll)
  useEffect(() => {
    const hasNewItems = itemsLength > refs.previousItemsLength.current
    const autoScrollTurnedOn = shouldAutoScroll && !previousAutoScroll.current
    previousAutoScroll.current = shouldAutoScroll
    refs.previousItemsLength.current = itemsLength
    if ((!hasNewItems && !autoScrollTurnedOn) || !shouldAutoScroll || refs.userScrolling.current) return
    if (refs.autoScrollFrame.current !== null) cancelAnimationFrame(refs.autoScrollFrame.current)
    refs.autoScrollFrame.current = requestAnimationFrame(() => {
      refs.autoScrollFrame.current = null
      if (refs.container.current && !refs.userScrolling.current) {
        refs.container.current.scrollTop = refs.container.current.scrollHeight
      }
    })
  }, [itemsLength, refs, shouldAutoScroll])
}

const useVirtualCleanup = (refs: VirtualRefs) => useEffect(() => () => {
  if (refs.userScrollTimer.current) clearTimeout(refs.userScrollTimer.current)
  if (refs.layoutFrame.current !== null) cancelAnimationFrame(refs.layoutFrame.current)
  if (refs.autoScrollFrame.current !== null) cancelAnimationFrame(refs.autoScrollFrame.current)
}, [refs])

export const useVirtualizedList = (props: VirtualizedListProps) => {
  const refs = useVirtualRefs(props.items.length)
  const state = useVirtualState({ autoScroll: props.autoScroll, onAutoScrollChange: props.onAutoScrollChange })
  const positions = usePositionModel({
    estimatedItemHeight: props.estimatedItemHeight, height: props.height, items: props.items, refs, state,
  })
  const scrollOptions = useMemo(() => ({ calculatePositions: positions.calculatePositions, refs, state }), [
    positions.calculatePositions, refs, state,
  ])
  const handleScroll = useScrollHandler(scrollOptions)
  const actions = useScrollActions(scrollOptions)
  useLayoutEffects(props.items, scrollOptions)
  useAutoScroll(props.items.length, refs, state)
  useVirtualCleanup(refs)
  return { actions, handleScroll, positions, refs, state }
}
