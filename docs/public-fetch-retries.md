# Bounded public HTTPS acquisition

The standalone public guard and direct HTTPS staging use the same Fetcher.
Every URL, expected byte count, SHA-256 and recipe expectation stays unchanged.

- At most four attempts per URL operation, with the existing 180-second
  per-open timeout and bounded response reads.
- Retry only HTTP 429, 500, 502, 503, 504 and urllib URLError transport failures.
  Other HTTP errors and local I/O errors fail immediately.
- Backoff is 1, 2 and 4 seconds. Numeric or HTTP-date Retry-After may increase a
  delay, but each delay is capped at 10 seconds (30 seconds total at most).
  Invalid or past headers cannot disable bounded backoff.
- Each retried body is reopened/truncated; partial bodies are never appended.
  SHA/size and recipe authentication happen outside the retry loop and never
  trigger a same-URL retry. Exhausted failures remove partial cache bodies.

PNG alternate URLs are limited to the declared alternatives, with the same
stable-byte authentication; the Fetcher adds no mirrors or source URLs.
Git/LFS transport behavior is unchanged. No catalog or provenance generation is
performed by fetching/staging.

This behavior is implemented by the immutable guard release
`repo-lfs-public-http-retry-v3`; earlier registry IDs and hashes remain valid.
Its canonical bytes are copied into task environments by
`tools.release_payload_guard`; task bindings and the reviewed source audit are
synchronized separately.
