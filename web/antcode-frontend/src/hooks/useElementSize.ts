import { useEffect, useRef, useState } from 'react'

interface ElementSize {
  width: number
  height: number
}

/**
 * 监听 DOM 元素尺寸变化（ResizeObserver）。
 * 用于需要根据父容器尺寸动态计算子元素布局的场景，例如表格 scroll.y。
 */
export function useElementSize<T extends HTMLElement = HTMLDivElement>(): [
  React.RefObject<T>,
  ElementSize,
] {
  const ref = useRef<T>(null)
  const [size, setSize] = useState<ElementSize>({ width: 0, height: 0 })

  useEffect(() => {
    const target = ref.current
    if (!target) return

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry) return
      const { width, height } = entry.contentRect
      setSize((prev) =>
        prev.width === width && prev.height === height ? prev : { width, height }
      )
    })

    observer.observe(target)
    return () => observer.disconnect()
  }, [])

  return [ref, size]
}
