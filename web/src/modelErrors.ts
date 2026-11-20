import type { ModelErrorInfo } from "./types"
import type { Lang } from "./i18n"

type Copy = { title: string; message: string; hint: string }

function vars(info: ModelErrorInfo): Record<string, string> {
  const params = info.params ?? {}
  const endpoint = info.endpoint || params.endpoint || "—"
  const model = info.model || params.model || "—"
  const provider = info.provider || params.provider || "—"
  const env = params.env || "SPARK_API_KEY"
  const status = info.status_code ? String(info.status_code) : params.status || ""
  const local = params.local === "1"
  return { endpoint, model, provider, env, status, local: local ? "1" : "0" }
}

function fill(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => values[key] ?? "")
}

const COPY: Record<string, Record<Lang, Copy>> = {
  missing_api_key: {
    zh: {
      title: "模型未配置：缺少 API Key",
      message: "当前 Provider 需要凭据，但配置里没有可用的 API Key，请求没有发出。",
      hint: "打开「设置 → 模型」填入 API Key，或设置环境变量 {env}；保存后点「测试连接」确认。",
    },
    en: {
      title: "Model not configured: API key missing",
      message: "This provider needs credentials, but no API key is configured. The request was not sent.",
      hint: "Open Settings → Model and add an API key, or set {env}. Save, then click Test connection.",
    },
  },
  mock_fallback: {
    zh: {
      title: "未配置 API Key，已阻止 Mock 回退",
      message: "配置指向真实模型，但缺少凭据。系统原本会退回 Mock 演示模型；为避免把演示回答当成真实结果，本轮已停止。",
      hint: "在「设置 → 模型」填入 API Key，或设置环境变量 {env}。只想体验界面时，请显式切换到 Mock 演示模型。",
    },
    en: {
      title: "Real model selected, but no API key",
      message: "The configured provider needs a key. Spark would have fallen back to a scripted mock answer, so this turn was stopped instead.",
      hint: "Add an API key in Settings → Model, or set {env}. To try the UI without a model, explicitly choose the Mock demo provider.",
    },
  },
  missing_model: {
    zh: {
      title: "模型未配置：缺少模型名",
      message: "Provider 已选中，但没有填写模型 ID，无法发起请求。",
      hint: "在「设置 → 模型」填写服务商提供的模型 ID，例如 deepseek-chat、qwen3-coder-plus 或本地模型文件名。",
    },
    en: {
      title: "Model not configured: model name missing",
      message: "A provider is selected, but the model ID is empty, so no request can be sent.",
      hint: "Fill in the model ID in Settings → Model, e.g. deepseek-chat, qwen3-coder-plus, or a local model name.",
    },
  },
  local_model_missing: {
    zh: {
      title: "本地模型还没有下载",
      message: "找不到本地 GGUF 文件，请求没有发出。",
      hint: "打开「设置 → 模型 → 本地模型」，下载 MiniCPM5-1B 等推荐模型，再点「使用此模型」。",
    },
    en: {
      title: "Local model is not downloaded",
      message: "The GGUF file is missing, so the request never started.",
      hint: "Open Settings → Model → Local models, download MiniCPM5-1B or another recommendation, then click Use this model.",
    },
  },
  tool_loop_limit: {
    zh: {
      title: "模型在工具调用里绕不出来",
      message: "模型连续多轮调用工具，仍未给出最终回答。工具可能已经执行成功，但小模型没有把结果收束成答案。",
      hint: "把任务拆成更小的一步，或换用 4B 级模型。工具结果已保留在工作区，不会重复执行。",
    },
    en: {
      title: "Model got stuck in a tool loop",
      message: "The model kept calling tools without producing a final answer. The tools may have succeeded, but the small model failed to summarise the result.",
      hint: "Split the task into a smaller step, or switch to a 4B model. Tool results are kept in the workspace and are not re-executed.",
    },
  },
  tool_repeat_loop: {
    zh: {
      title: "模型重复调用同一个工具",
      message: "模型连续用完全相同的参数调用同一个工具，已触发保护阈值。",
      hint: "重复调用已被拦截，不会污染工作区；换一个说法或换用 4B 级模型继续。",
    },
    en: {
      title: "Model repeated the same tool call",
      message: "The model called the same tool with identical arguments until the safety threshold fired.",
      hint: "The duplicate calls were blocked and did not touch the workspace. Rephrase the task or switch to a 4B model.",
    },
  },
  local_model_load_failed: {
    zh: {
      title: "本地模型加载失败",
      message: "GGUF 文件存在，但 llama.cpp 没能加载它；常见原因是文件不完整、格式不支持或内存不足。",
      hint: "删除后重新下载；确认运行库支持该模型；降低 SPARK_LOCAL_CTX 或关闭占内存的程序。",
    },
    en: {
      title: "Local model failed to load",
      message: "The GGUF file exists but llama.cpp could not load it — usually a partial download, unsupported quant, or low memory.",
      hint: "Delete and re-download; verify the runtime supports this model; lower SPARK_LOCAL_CTX or free memory.",
    },
  },
  missing_base_url: {
    zh: {
      title: "模型未配置：缺少服务地址",
      message: "没有填写 OpenAI 兼容端点地址，请求无法定位到模型服务。",
      hint: "在「设置 → 模型」填写 Base URL；Ollama 通常是 http://127.0.0.1:11434/v1，llama.cpp 通常是 http://127.0.0.1:8010/v1。",
    },
    en: {
      title: "Model not configured: endpoint missing",
      message: "The OpenAI-compatible endpoint is empty, so Spark cannot reach a model service.",
      hint: "Set the Base URL in Settings → Model. Ollama is usually http://127.0.0.1:11434/v1; llama.cpp is usually http://127.0.0.1:8010/v1.",
    },
  },
  auth_failed: {
    zh: {
      title: "模型鉴权失败",
      message: "{provider} 拒绝了这组凭据（HTTP {status}），请求没有进入推理。",
      hint: "检查 API Key 是否过期、复制完整，以及账号是否有 {model} 的权限；环境变量名应为 {env}。",
    },
    en: {
      title: "Model authentication failed",
      message: "{provider} rejected the credentials (HTTP {status}). The request never reached inference.",
      hint: "Check whether the API key is expired or truncated, and whether the account can access {model}. The environment variable should be {env}.",
    },
  },
  model_not_found: {
    zh: {
      title: "模型不存在或无权访问",
      message: "服务端找不到模型「{model}」，请求没有进入推理。",
      hint: "先核对模型 ID 的拼写，并到服务商控制台确认该模型已开通。",
    },
    en: {
      title: "Model not found or not available",
      message: "The endpoint cannot find model “{model}”. No inference was started.",
      hint: "Check the model ID spelling and confirm the model is enabled in the provider console.",
    },
  },
  rate_limited: {
    zh: {
      title: "请求被限流",
      message: "模型服务暂时限流或并发超限，这次请求被拒绝。",
      hint: "等待几十秒后重试；持续限流时降低并发、换用其他模型档案，或升级服务商套餐。",
    },
    en: {
      title: "Rate limited",
      message: "The model service is throttling requests or hit a concurrency limit.",
      hint: "Wait a few seconds and retry. If it persists, lower concurrency, switch model profiles, or upgrade your provider plan.",
    },
  },
  quota_exceeded: {
    zh: {
      title: "模型额度不足",
      message: "服务商返回余额或配额不足，当前账号无法继续调用。",
      hint: "到服务商控制台充值或确认套餐额度；也可以切换到其他模型档案。",
    },
    en: {
      title: "Model quota exhausted",
      message: "The provider reports insufficient balance or quota for this account.",
      hint: "Top up or check your plan in the provider console, or switch to another model profile.",
    },
  },
  context_too_long: {
    zh: {
      title: "上下文超出模型窗口",
      message: "这轮请求超过了 {model} 的上下文上限，模型拒绝了请求。",
      hint: "新建会话或清空较早消息；也可以在「设置 → Agent」调低历史预算，或换用上下文更大的模型。",
    },
    en: {
      title: "Context exceeds the model window",
      message: "This turn is larger than {model}'s context limit, so the model rejected it.",
      hint: "Start a new session or clear older messages. You can also lower the history budget in Settings → Agent, or switch to a model with a larger window.",
    },
  },
  timeout: {
    zh: {
      title: "模型响应超时",
      message: "请求在等待模型返回时超时，可能是网络慢、模型排队或本地机器负载过高。",
      hint: "先重试一次；本地模型可减少上下文或线程数，远程模型检查网络与代理。",
    },
    en: {
      title: "Model request timed out",
      message: "The request timed out while waiting for the model. The network may be slow, the model may be queued, or the local machine may be overloaded.",
      hint: "Retry once. For local models, reduce context or threads; for remote models, check the network and proxy.",
    },
  },
  connection_failed: {
    zh: {
      title: "连接不上模型服务",
      message: "请求没有到达 {endpoint}，通常是服务未启动、地址/端口写错或网络被拦截。",
      hint: "本地模型：确认 Ollama / LM Studio / llama.cpp 已启动且端口正确，用 curl 打开 /v1/models 验证。远程模型：检查 Base URL、网络、代理和防火墙。",
    },
    en: {
      title: "Cannot reach the model service",
      message: "The request never reached {endpoint}. The service may be down, the address/port may be wrong, or the network may be blocked.",
      hint: "Local model: confirm Ollama / LM Studio / llama.cpp is running on the right port, then curl /v1/models. Remote: check the Base URL, network, proxy, and firewall.",
    },
  },
  endpoint_not_found: {
    zh: {
      title: "模型端点路径不对",
      message: "服务端返回 404，{endpoint} 可能不是正确的 OpenAI 兼容地址。",
      hint: "多数 OpenAI 兼容端点需要以 /v1 结尾（例如 http://127.0.0.1:8010/v1）；检查 Base URL 是否多写或少写路径。",
    },
    en: {
      title: "Model endpoint path is wrong",
      message: "The server returned 404. {endpoint} may not be an OpenAI-compatible endpoint.",
      hint: "Most OpenAI-compatible endpoints end with /v1 (e.g. http://127.0.0.1:8010/v1). Check whether the Base URL path is missing or has an extra segment.",
    },
  },
  server_error: {
    zh: {
      title: "模型服务内部错误",
      message: "服务端返回 HTTP {status}，这次推理没有完成。",
      hint: "稍后重试；如果持续出现，查看模型服务日志，或切换到备用模型/端点。",
    },
    en: {
      title: "Model service internal error",
      message: "The server returned HTTP {status}, so inference did not complete.",
      hint: "Retry shortly. If it persists, check the model server logs or switch to a fallback model/endpoint.",
    },
  },
  invalid_request: {
    zh: {
      title: "模型拒绝了请求参数",
      message: "端点认为请求格式或参数不合法，常见于模型不支持当前工具、图片或参数组合。",
      hint: "确认 {model} 支持工具调用；在设置里关闭不兼容参数，或换用兼容模型。",
    },
    en: {
      title: "Model rejected the request parameters",
      message: "The endpoint considers the request invalid. This often means the model does not support the current tools, images, or parameter combination.",
      hint: "Confirm {model} supports tool calls, disable incompatible parameters, or switch to a compatible model.",
    },
  },
  cancelled: {
    zh: {
      title: "请求已取消",
      message: "本轮生成被用户或客户端中断，没有产生完整回答。",
      hint: "可以直接重新发送；如果重复中断，检查网络或客户端连接。",
    },
    en: {
      title: "Request cancelled",
      message: "The turn was interrupted by the user or client, so no complete answer was produced.",
      hint: "Send it again. If it keeps getting cancelled, check the network or client connection.",
    },
  },
  turn_failed: {
    zh: {
      title: "请求未完成",
      message: "本轮没有正常结束。",
      hint: "可以重试；如果重复失败，到「设置 → 模型」检查连接。",
    },
    en: {
      title: "Request did not complete",
      message: "This turn ended before a complete answer was produced.",
      hint: "Retry. If it keeps failing, check the connection in Settings → Model.",
    },
  },
  unknown: {
    zh: {
      title: "模型调用失败",
      message: "模型请求没有完成，错误不在常见分类里。",
      hint: "展开技术细节查看原始错误；先重试一次，仍失败就到「设置 → 模型」点「测试连接」并检查服务日志。",
    },
    en: {
      title: "Model call failed",
      message: "The model request did not complete, and the error did not match a common category.",
      hint: "Expand Technical details, retry once, then use Test connection in Settings → Model and check the server logs.",
    },
  },
}

export function modelErrorText(info: ModelErrorInfo | undefined, lang: Lang, fallbackText = ""): Copy {
  if (!info) {
    return {
      title: lang === "zh" ? "请求未完成" : "Request did not complete",
      message: fallbackText || (lang === "zh" ? "本轮没有正常结束。" : "This turn ended unexpectedly."),
      hint: lang === "zh" ? "可以重试这次请求。" : "You can retry this request.",
    }
  }
  const values = vars(info)
  // Unknown failures carry the sanitized provider exception in `message`.
  // Prefer it over the generic copy so a provider crash is debuggable from
  // the chat card without opening the technical-details drawer.
  if (info.code === "unknown") {
    const unknown = COPY.unknown[lang]
    return {
      title: unknown.title,
      message: info.message || unknown.message,
      hint: info.hint || unknown.hint,
    }
  }
  const copy = COPY[info.code]?.[lang]
  if (!copy) {
    return {
      title: info.title || (lang === "zh" ? "模型调用失败" : "Model call failed"),
      message: info.message || fallbackText,
      hint: info.hint || "",
    }
  }
  let hint = copy.hint
  if (info.code === "model_not_found" && values.local === "1") {
    hint =
      lang === "zh"
        ? `本地服务已响应，但没有模型「${values.model}」。Ollama 用 ollama list 查看、ollama pull ${values.model} 拉取；llama.cpp 检查 --model 指向的 GGUF 文件名。`
        : `The local server responded but has no model “${values.model}”. For Ollama, run ollama list and ollama pull ${values.model}. For llama.cpp, check the GGUF filename passed to --model.`
  }
  return {
    title: fill(copy.title, values),
    message: fill(copy.message, values),
    hint: fill(hint, values),
  }
}

/** Short code label for badges and screen readers. */
export function modelErrorCodeLabel(code: string, lang: Lang): string {
  const labels: Record<string, [string, string]> = {
    missing_api_key: ["未配置 Key", "No API key"],
    mock_fallback: ["Mock 回退已阻止", "Mock fallback blocked"],
    missing_model: ["未配置模型", "No model"],
    missing_base_url: ["未配置地址", "No endpoint"],
    local_model_missing: ["模型未下载", "Model not downloaded"],
    local_model_load_failed: ["本地加载失败", "Local load failed"],
    tool_loop_limit: ["工具循环", "Tool loop"],
    tool_repeat_loop: ["重复调用", "Repeat call"],
    auth_failed: ["鉴权失败", "Auth failed"],
    model_not_found: ["模型不存在", "Model not found"],
    rate_limited: ["限流", "Rate limited"],
    quota_exceeded: ["额度不足", "Quota"],
    context_too_long: ["上下文超限", "Context too long"],
    timeout: ["超时", "Timeout"],
    connection_failed: ["无法连接", "Unreachable"],
    endpoint_not_found: ["端点错误", "Bad endpoint"],
    server_error: ["服务错误", "Server error"],
    invalid_request: ["请求不兼容", "Invalid request"],
    cancelled: ["已取消", "Cancelled"],
    turn_failed: ["未完成", "Failed"],
    unknown: ["未知错误", "Unknown"],
  }
  const pair = labels[code]
  return pair ? pair[lang === "zh" ? 0 : 1] : code
}
