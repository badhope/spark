import { useEffect, useState } from "react"

const MOBILE_QUERY = "(max-width: 767px)"

/** True for the phone layout (≤767px). Safe under SSR/test environments. */
export function useIsMobile(query = MOBILE_QUERY): boolean {
  const read = () => {
    try {
      return window.matchMedia(query).matches
    } catch {
      return false
    }
  }
  const [mobile, setMobile] = useState(read)
  useEffect(() => {
    let mql: MediaQueryList
    try {
      mql = window.matchMedia(query)
    } catch {
      return undefined
    }
    const onChange = () => setMobile(mql.matches)
    setMobile(mql.matches)
    mql.addEventListener("change", onChange)
    return () => mql.removeEventListener("change", onChange)
  }, [query])
  return mobile
}
