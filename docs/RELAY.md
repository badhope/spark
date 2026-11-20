# Outbound relay

Spark can expose its local Web API to a remote client without opening an
inbound port. The Spark process dials **out** to a relay, polls for requests,
forwards each request to its loopback Web server, and posts the response back.

```text
remote client ──HTTP──▶ relay ◀──long-poll── Spark process ──loopback──▶ Spark Web
```

The relay never talks to a model and never keeps a request or response after it
has been delivered. Request and response bodies are base64 encoded so JSON and
binary payloads survive the hop.

## Start the relay

```bash
spark relay serve --host 127.0.0.1 --port 8788
```

The relay is **plaintext HTTP**. Bind it to loopback or a private network, and
put a TLS-terminating reverse proxy in front for internet access. Do not expose
`--host 0.0.0.0` directly.

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

`--upstream` must point at the local Spark Web server. If that server requires
an `X-Spark-Token` or a paired-device token, pass it with `--token` (or
`SPARK_WEB_TOKEN`) and the agent injects it after receiving the relayed request.
The relay key and the Spark token are separate credentials: the relay key
protects the channel, while Spark still checks its own token.

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
`--body-file` for POST/PUT/PATCH bodies. The remote client can also speak the
HTTP protocol directly:

```text
POST /v1/channels/{channel}/call
X-Relay-Key: <key>
Content-Type: application/json

{"method":"GET","path":"/api/status","headers":{},"body_b64":""}
```

## Protocol

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/channels/{id}/register` | Register a channel with its key (first caller wins). |
| `GET` | `/v1/channels/{id}/next?wait=25` | Agent long-poll; returns one request or `204`. |
| `POST` | `/v1/channels/{id}/responses` | Agent posts `{request_id,status,headers,body_b64}`. |
| `POST` | `/v1/channels/{id}/call` | Client submits a request and waits for the response. |

The channel id must be 8–64 characters (`A-Z`, `a-z`, `0-9`, `_`, `-`). Keys
must be at least 16 characters; only their SHA-256 digest is stored in relay
memory. A channel is considered unavailable when no agent has polled within 45
seconds.

## Limits

- This MVP buffers responses; **SSE event streams are not relayed**. Use it for
  JSON and binary API calls. A streaming relay is a W7 follow-up.
- The relay keeps channels in memory. Restarting it requires reconnecting the
  agent.
- The relay is not a replacement for the existing loopback token model. Keep
  `X-Spark-Token` / device pairing enabled on the local Web server.
- The remote client must trust the TLS reverse proxy; Spark does not implement
  certificate pinning in this MVP.
