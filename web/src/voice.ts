import { useEffect, useRef, useState } from "react"
import type { Lang } from "./i18n"

interface SpeechAlternative {
  transcript: string
}
interface SpeechResult {
  isFinal: boolean
  length: number
  0: SpeechAlternative
}
interface SpeechResultList {
  length: number
  [index: number]: SpeechResult
}
interface SpeechEvent {
  resultIndex: number
  results: SpeechResultList
}
interface SpeechRecognitionLike {
  lang: string
  continuous: boolean
  interimResults: boolean
  onresult: ((event: SpeechEvent) => void) | null
  onerror: ((event: { error?: string }) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}
type SpeechCtor = new () => SpeechRecognitionLike

function speechCtor(): SpeechCtor | undefined {
  if (typeof window === "undefined") return undefined
  const w = window as unknown as {
    SpeechRecognition?: SpeechCtor
    webkitSpeechRecognition?: SpeechCtor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition
}

/**
 * Browser SpeechRecognition adapter.
 * - No cloud key, no extra dependency: use the browser's own recognizer.
 * - On unsupported / denied / no-speech, callers keep the textarea usable.
 */
export function useVoiceInput(
  lang: Lang,
  onText: (text: string) => void,
  onError: (code: string) => void,
) {
  const [supported] = useState(() => Boolean(speechCtor()))
  const [listening, setListening] = useState(false)
  const recRef = useRef<SpeechRecognitionLike | null>(null)
  const onTextRef = useRef(onText)
  const onErrorRef = useRef(onError)
  onTextRef.current = onText
  onErrorRef.current = onError

  useEffect(() => {
    return () => {
      recRef.current?.abort()
      recRef.current = null
    }
  }, [])

  function start() {
    if (listening) return
    const Ctor = speechCtor()
    if (!Ctor) {
      onErrorRef.current("unsupported")
      return
    }
    const rec = new Ctor()
    rec.lang = lang === "zh" ? "zh-CN" : "en-US"
    rec.continuous = false
    rec.interimResults = true
    rec.onresult = (event) => {
      let finalText = ""
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i]
        if (result?.isFinal && result[0]?.transcript) finalText += result[0].transcript
      }
      const clean = finalText.trim()
      if (clean) onTextRef.current(clean)
    }
    rec.onerror = (event) => {
      // "no-speech" and "aborted" are ordinary user outcomes, not feature failures.
      const code = event.error ?? "speech_error"
      if (code !== "no-speech" && code !== "aborted") onErrorRef.current(code)
    }
    rec.onend = () => {
      if (recRef.current === rec) recRef.current = null
      setListening(false)
    }
    recRef.current = rec
    try {
      rec.start()
      setListening(true)
    } catch {
      recRef.current = null
      setListening(false)
      onErrorRef.current("start_failed")
    }
  }

  function stop() {
    recRef.current?.stop()
    setListening(false)
  }

  return {
    supported,
    listening,
    toggle: () => (listening ? stop() : start()),
    stop,
  }
}
