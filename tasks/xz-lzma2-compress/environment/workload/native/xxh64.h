/* Minimal streaming XXH64 (seed 0), the public xxHash 64-bit algorithm.
 * Protected driver code: commits to every decoded byte without copying the
 * stream into the page cache. Test vectors: "" -> ef46db3751d8e999,
 * "abc" -> 44bc2cf5ad770999. */
#ifndef PROTO_XXH64_H
#define PROTO_XXH64_H
#include <stdint.h>
#include <string.h>
#define P1 0x9E3779B185EBCA87ULL
#define P2 0xC2B2AE3D27D4EB4FULL
#define P3 0x165667B19E3779F9ULL
#define P4 0x85EBCA77C2B2AE63ULL
#define P5 0x27D4EB2F165667C5ULL
typedef struct { uint64_t v[4], total; uint8_t buf[32]; size_t n; } xxh64_state;
static inline uint64_t xrotl(uint64_t x, int r) { return (x << r) | (x >> (64 - r)); }
static inline uint64_t xrd64(const uint8_t *p) { uint64_t v; memcpy(&v, p, 8); return v; }
static inline uint32_t xrd32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }
static inline uint64_t xround(uint64_t a, uint64_t in) { a += in * P2; a = xrotl(a, 31); return a * P1; }
static inline uint64_t xmerge(uint64_t h, uint64_t v) { h ^= xround(0, v); return h * P1 + P4; }
static void xxh64_init(xxh64_state *s)
{
    s->v[0] = P1 + P2; s->v[1] = P2; s->v[2] = 0; s->v[3] = (uint64_t)0 - P1;
    s->total = 0; s->n = 0;
}
static void xxh64_update(xxh64_state *s, const uint8_t *p, size_t len)
{
    s->total += len;
    if (s->n + len < 32) { memcpy(s->buf + s->n, p, len); s->n += len; return; }
    if (s->n) {
        size_t k = 32 - s->n;
        memcpy(s->buf + s->n, p, k); p += k; len -= k;
        for (int i = 0; i < 4; i++) s->v[i] = xround(s->v[i], xrd64(s->buf + 8 * i));
        s->n = 0;
    }
    uint64_t v0 = s->v[0], v1 = s->v[1], v2 = s->v[2], v3 = s->v[3];
    while (len >= 32) {
        v0 = xround(v0, xrd64(p)); v1 = xround(v1, xrd64(p + 8));
        v2 = xround(v2, xrd64(p + 16)); v3 = xround(v3, xrd64(p + 24));
        p += 32; len -= 32;
    }
    s->v[0] = v0; s->v[1] = v1; s->v[2] = v2; s->v[3] = v3;
    memcpy(s->buf, p, len); s->n = len;
}
static uint64_t xxh64_final(const xxh64_state *s)
{
    uint64_t h;
    if (s->total >= 32) {
        h = xrotl(s->v[0], 1) + xrotl(s->v[1], 7) + xrotl(s->v[2], 12) + xrotl(s->v[3], 18);
        for (int i = 0; i < 4; i++) h = xmerge(h, s->v[i]);
    } else {
        h = s->v[2] + P5;
    }
    h += s->total;
    const uint8_t *p = s->buf; size_t len = s->n;
    while (len >= 8) { h ^= xround(0, xrd64(p)); h = xrotl(h, 27) * P1 + P4; p += 8; len -= 8; }
    if (len >= 4) { h ^= (uint64_t)xrd32(p) * P1; h = xrotl(h, 23) * P2 + P3; p += 4; len -= 4; }
    while (len) { h ^= (*p) * P5; h = xrotl(h, 11) * P1; p++; len--; }
    h ^= h >> 33; h *= P2; h ^= h >> 29; h *= P3; h ^= h >> 32;
    return h;
}
#endif
