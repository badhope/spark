# Outbound relay

Spark can expose its local Web API to a remote client without opening an
inbound port. The Spark process dials **out** to a relay, polls for requests,
forwards each request to its loopback Web server, and posts the response back.

```text
remote client ──HTTP──▶ relay ◀──long-poll── Spark process ──loopback──▶ Spark Web
```

The relay never talks to a model and never keeps a request or response after it
has been delivered. Request and response bodies are base64 encoded so JSON and
binary payloads survive the hop. Streaming responses use a second rendezvous:
the agent reads the upstream with `httpx.stream` and posts headers, chunks and
an end marker; the relay keeps the remote connection open and writes HTTP
chunked encoding as the bytes arrive.

## Start the relay

```bash
export SPARK_RELAY_KEY="$(openssl rand -hex 24)"
CHANNEL="$(openssl rand -hex 12)"

spark relay serve --host 127.0.0.1 --port 8788 \
  --channel "$CHANNEL" --key "$SPARK_RELAY_KEY"
```

With `--channel` + `--key`, the relay root (`/`) becomes a Web entry for that
channel: `/`, `/assets/*`, `/sw.js` and `/api/*` are forwarded to the agent's
loopback Spark Web. All paths are used as-is, so the SPA's absolute asset and
API URLs work without rewriting. Requests whose path ends in `/stream` (or
carry `Accept: text/event-stream`) use the chunked streaming path.

One relay process exposes one channel at root. The relay is **plaintext HTTP**;
bind it to loopback or a private network, and put a TLS-terminating reverse
proxy in front for internet access. Do not expose `--host 0.0.0.0` directly.

## Start the local agent

In the machine that runs Spark Web:

```bash
export SPARK_RELAY_KEY="$(openssl rand -hex 24)"
CHANNEL="$(openssl rand -hex 12)"

spark relay connect \
  --relay https://relay.example.com \
  --channel "$CHANNEL" \
  --key "$SPARK_RELAY_KEY" \
  --upstream http://127.0.0.1:8000
```

The relay and agent must use the same channel/key. `--upstream` must point at
the local Spark Web server. Remote callers must use a **paired-device token**,
passed with `--token` (or `SPARK_WEB_TOKEN`); the agent injects it after
receiving the relayed request. A browser ordinarily gets that token by claiming
a one-time pairing code through the relay root. The relay key and the Spark
device token are separate credentials: the relay key protects the channel and
is never sent to the browser, while Spark still enforces device scope. A
relayed request is marked with `X-Spark-Relay: 1`, so the local **break-glass
control token is rejected** even though the agent reaches the Web server over
loopback.

To make the Spark UI generate relay-based pairing links/QR codes, start Web
with the relay origin as its public URL:

```bash
spark web --public-url https://relay.example.com
```

The remote panel then produces `https://relay.example.com/#pair=<code>`; the
phone opens the relay root, claims the one-time code, stores the device token
and keeps using the same relay origin for API/SSE calls.

## Call through the relay

```bash
spark relay call \
  --relay https://relay.example.com \
  --channel "$CHANNEL" \
  --key "$SPARK_RELAY_KEY" \
  --method GET \
  --path /api/status
```

A non-2xx upstream status is printed and the command exits non-zero. Use
`--body-file` for POST/PUT/PATCH bodies. Add `--stream` for SSE or chunked
responses; bytes are printed as they arrive instead of after the turn ends:

```bash
spark relay call \
  --relay https://relay.example.com \
  --channel "$CHANNEL" \
  --key "$SPARK_RELAY_KEY" \
  --method POST \
  --path /api/chat/stream \
  --stream
```

The remote client can also speak the HTTP protocol directly:

```text
POST /v1/channels/{channel}/call
X-Relay-Key: <key>
Content-Type: application/json

{"method":"GET","path":"/api/status","headers":{},"body_b64":""}
```

For streaming, add `"stream": true` and keep the response connection open;
the relay returns `Transfer-Encoding: chunked` and forwards each upstream
chunk without waiting for the final event.

## Protocol

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/channels/{id}/register` | Register a channel with its key (first caller wins). |
| `GET` | `/v1/channels/{id}/next?wait=25` | Agent long-poll; returns one request or `204`. |
| `POST` | `/v1/channels/{id}/responses` | Agent posts `{request_id,status,headers,body_b64}`. |
| `POST` | `/v1/channels/{id}/stream` | Agent posts `{request_id,type:headers/chunk/end/error,...}` for a streaming call. |
| `POST` | `/v1/channels/{id}/call` | Client submits a request and waits for the response; `{"stream":true}` keeps it chunked. |

The channel id must be 8–64 characters (`A-Z`, `a-z`, `0-9`, `_`, `-`). Keys
must be at least 16 characters; only their SHA-256 digest is stored in relay
memory. A channel is considered unavailable when no agent has polled within 45
seconds.

## Limits

- Streaming supports HTTP/1.1 chunked bodies, including SSE. It does not
  multiplex WebSocket; each stream is one request/connection. A stream with no
  headers or no new chunk for 60 seconds is closed. Per-chunk posts add relay
  overhead, so very high-frequency token streams should batch in the caller.
- The relay keeps channels in memory. Restarting it requires reconnecting the
  agent.
- The relay is not a replacement for the existing loopback token model. The
  relay root is reachable by anyone who can reach the relay; static files are
  public, while Spark still enforces `X-Spark-Token` / device pairing for
  reads/writes and rejects the break-glass control token on relayed requests.
- The remote client must trust the TLS reverse proxy; Spark does not implement
  certificate pinning in this MVP.
- One relay process exposes one Web channel at `/`. Multi-tenant hosted relay,
  wildcard subdomains and account login are intentionally not part of this
  self-hosted entry.
