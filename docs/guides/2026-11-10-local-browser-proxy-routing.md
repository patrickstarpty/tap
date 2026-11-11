# Local browser routing through an existing proxy

## Scope

The local Browser runtime has an optional transport adapter for machines using a loopback HTTP proxy and synthetic DNS (Fake-IP). It keeps the task policy proxy as the browser's only route. It does not modify macOS proxy, DNS, VPN/TUN, firewall or trust settings, and it does not allow private/Fake-IP destinations.

The default direct transport is unchanged. This is local development support, not a change to the Docker network boundary or to the session's `declared_origins` / `public_web` policy.

## Supervisor configuration

The supervisor can supply `TAP_BROWSER_LOCAL_UPSTREAM_PROXY`, or construct `LocalBrowserRuntime(upstream_proxy=...)`. For an already configured proxy listening at port 7897, the value is `http://127.0.0.1:7897`.

Only an explicit loopback IPv4 HTTP endpoint without credentials is accepted. The option is not accepted from a flow, browser action or frontend. It reaches only the owned policy proxy's startup configuration, and is removed before inspector startup. Inherited HTTP_PROXY/HTTPS_PROXY/ALL_PROXY settings are not automatically adopted. PAC, authenticated upstreams and SOCKS upstreams are not supported by this adapter. A synthetic test target cannot be combined with this mode.

No actual `.env` file is changed by the implementation. The upstream must be an existing trusted local proxy; this adapter does not install or configure one.

## Destination validation

In upstream mode, the task proxy obtains A records from Cloudflare's documented DNS-over-HTTPS endpoint over authenticated TLS, connecting through the existing local proxy to the fixed public bootstrap address `1.1.1.1`. This sends the destination's DNS name to that resolver, not browser input values or cookies.

The exact DNS question, complete CNAME chain and all A records are validated. Private, loopback, metadata, benchmark/Fake-IP and excluded reserved/documentation ranges remain denied. Truncated, malformed, oversized or redirected resolver responses fail closed.

Each target connection uses upstream CONNECT to the validated **numeric IPv4 address**, not to a hostname that the upstream could resolve again. The target's original HTTP Host and TLS SNI are retained; certificate verification stays enabled. Existing inner Host/SNI/CONNECT consistency and origin checks remain in force. Resolution, CONNECT and TLS failures do not trigger a direct or hostname-based fallback.

The upstream is a trusted routing component. Numeric CONNECT prevents this application from delegating target DNS to it; it does not remotely attest the proxy implementation or the user's network infrastructure.

## Why ordinary browsing and LCA previously differed

The observed Mac used Clash Verge/Mihomo Fake-IP DNS. `www.aia.com.hk` resolved to a synthetic `198.18.0.39`, not AIA's actual server address. Ordinary browsing could use the already configured proxy, whereas the original local task proxy used system DNS followed by a direct numeric connection. Its rejection of the synthetic address was correct, but that transport did not support the machine's proxy route.

The adapter resolves that transport mismatch without a `198.18` exception and without changing system settings. Historical Codex page access is not equivalent to a successful LCA quote or replay. In particular, the November 9 AIA reference reached the result page with Code903 and a blank premium; the earlier HK$45 observation is not a current quote.

## Verification boundary

Owned local fixtures cover public-IP validation, mixed/private DNS answers, alias chains, numeric CONNECT, Host/SNI and certificate mismatch, refused/oversized/redirected responses, bounded timeouts, policy proxy integration, rebinding resistance and supervisor environment isolation. Direct-mode fixtures remain covered.

Real FWD and AIA LCA recording/replay results are separate acceptance evidence. A successful page-load smoke is not a quotation pass. Synthetic inputs only are permitted for those runs; no application, contact details, payment, real personal data or paid model call is part of them. CAPTCHA or an explicit site restriction must not be bypassed.

## References

- [ADR-039: public website policy](../decisions/2026-11-08-adr-039-browser-public-web-policy.md)
- [Cloudflare DoH API](https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/)
- [Cloudflare resolver addresses](https://developers.cloudflare.com/1.1.1.1/ip-addresses/)
