import { fetchPushStatus, subscribePush, unsubscribePush, type PushStatus } from "./api"
import { getDefaultLang, t } from "./i18n"

function urlBase64ToUint8Array(value: string): Uint8Array {
  const clean = value.replace(/-/g, "+").replace(/_/g, "/")
  const padded = clean + "=".repeat((4 - (clean.length % 4)) % 4)
  const raw = atob(padded)
  const bytes = new Uint8Array(raw.length)
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i)
  return bytes
}

export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    window.isSecureContext &&
    "serviceWorker" in navigator &&
    "PushManager" in window
  )
}

export function notificationSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window
}

async function registration(): Promise<ServiceWorkerRegistration> {
  if (!pushSupported()) throw new Error(t("push.unsupported", getDefaultLang()))
  const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" })
  await navigator.serviceWorker.ready
  return reg
}

/** Ask for notification permission and subscribe this browser to Spark push. */
export async function enablePush(): Promise<PushStatus> {
  if (!notificationSupported()) throw new Error(t("push.notificationUnsupported", getDefaultLang()))
  const permission = await Notification.requestPermission()
  if (permission !== "granted") throw new Error(t("push.permissionDenied", getDefaultLang()))
  const status = await fetchPushStatus()
  if (!status.available) throw new Error(t("push.missingDependency", getDefaultLang()))
  if (!status.public_key) throw new Error(t("push.missingKey", getDefaultLang()))
  const reg = await registration()
  let subscription = await reg.pushManager.getSubscription()
  if (!subscription) {
    subscription = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(status.public_key) as BufferSource,
    })
  }
  const result = await subscribePush(subscription.toJSON())
  return result.status
}

export async function disablePush(): Promise<PushStatus> {
  const reg = await registration()
  const subscription = await reg.pushManager.getSubscription()
  if (subscription) {
    await unsubscribePush(subscription.endpoint)
    await subscription.unsubscribe()
  }
  return fetchPushStatus()
}

export async function currentPushSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null
  try {
    const reg = await navigator.serviceWorker.getRegistration("/")
    return reg ? await reg.pushManager.getSubscription() : null
  } catch {
    return null
  }
}
