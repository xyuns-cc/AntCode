import { useEffect, useState } from 'react'

export const useCurrentTime = (): Date => {
  const [currentTime, setCurrentTime] = useState(new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setCurrentTime(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return currentTime
}
