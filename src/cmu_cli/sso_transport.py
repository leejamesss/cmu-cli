"""Single-hop, identity-encoded, bounded browser route response transport.

No ambient proxy/netrc, redirects, cookie jar replay, or response decompression.
The caller must approve the URL/method/frame BEFORE invoking this function.
"""

import time

import requests

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_CONTEXT_BYTES = 32 * 1024 * 1024
MAX_CONTEXT_REQUESTS = 128


class SingleHopSession(requests.Session):
    def resolve_redirects(self, *args, **kwargs):
        # Requests otherwise consumes redirect bodies to prepare Response.next,
        # even with allow_redirects=False, defeating the streaming byte limit.
        return iter(())


def bounded_response(request, *, limit=MAX_RESPONSE_BYTES, timeout=30):
    """Read at most limit+1 wire bytes; never call buffered response.content.

    Identity-only responses avoid decompression bombs. Requests' connect/read
    timeout plus a monotonic deadline bounds slow trickles between chunks.
    Duplicate Set-Cookie fields retain separate lines for Chromium fulfillment.
    """
    deadline = time.monotonic() + timeout
    headers = request.all_headers()
    headers["accept-encoding"] = "identity"
    with SingleHopSession() as client:
        client.trust_env = False
        with client.request(
            request.method,
            request.url,
            headers=headers,
            data=request.post_data_buffer,
            allow_redirects=False,
            stream=True,
            timeout=timeout,
        ) as response:
            encoding = response.headers.get("Content-Encoding", "identity").lower()
            if encoding not in {"", "identity"}:
                raise ValueError("Unsupported response encoding")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) > limit):
                raise ValueError("Response exceeds byte limit")
            body = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError("Response deadline exceeded")
                chunk = response.raw.read(
                    min(65536, limit + 1 - len(body)), decode_content=False
                )
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > limit:
                    raise ValueError("Response exceeds byte limit")
            output_headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower()
                not in {"transfer-encoding", "content-length", "connection"}
            }
            cookies = response.raw.headers.getlist("Set-Cookie")
            if cookies:
                output_headers["Set-Cookie"] = "\n".join(cookies)
            return {
                "status": response.status_code,
                "headers": output_headers,
                "body": bytes(body),
            }
