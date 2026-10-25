#ifndef KICKMIX_SIMD_128_NEON_H
#define KICKMIX_SIMD_128_NEON_H

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <ostream>
#include <random>

#if defined(__aarch64__) && defined(__ARM_NEON)
#include <arm_neon.h>
#endif

namespace kickmix {

#if defined(__aarch64__) && defined(__ARM_NEON)
/// 128-bit word backed by one AArch64 NEON register.
///
/// Mirrors `b128_sse` lane for lane: lane 0 holds the lowest 64 bits, and the 32-bit
/// operations act on the four 32-bit lanes of the same register. Shift amounts are
/// passed as per-lane signed counts (`USHL`), so run-time shift amounts need no
/// immediate; a negative count performs the logical right shift.
struct alignas(sizeof(uint64_t) * 2) b128_neon {
    uint64x2_t v;

    constexpr static size_t BIT_SIZE = 128;
    constexpr static const char *NAME = "b128_neon";
    bool non_zero() const {
        return vmaxvq_u32(vreinterpretq_u32_u64(v)) != 0;
    }
    static b128_neon random(std::mt19937_64 &rng) {
        uint64_t lo = rng();
        uint64_t hi = rng();
        return {vcombine_u64(vcreate_u64(lo), vcreate_u64(hi))};
    }
    void randomize(std::mt19937_64 &rng) {
        uint64_t lo = rng();
        uint64_t hi = rng();
        v = vcombine_u64(vcreate_u64(lo), vcreate_u64(hi));
    }
    inline uint64_t u64(size_t k) const {
        uint64_t words[2];
        memcpy(words, &v, sizeof(words));
        return words[k];
    }
    inline uint32_t u32(size_t k) const {
        uint32_t words[4];
        memcpy(words, &v, sizeof(words));
        return words[k];
    }
    inline bool bit(size_t k) const {
        return (u64(k / 64) >> (k % 64)) & 1;
    }
    inline void set_bit(size_t k, bool new_value) {
        uint64_t words[2];
        memcpy(words, &v, sizeof(words));
        uint64_t mask = uint64_t{1} << (k % 64);
        words[k / 64] = (words[k / 64] & ~mask) | ((uint64_t)new_value << (k % 64));
        v = vld1q_u64(words);
    }
    inline void clear_to_zero() {
        v = vdupq_n_u64(0);
    }
    inline void clear_to_max() {
        v = vdupq_n_u64(UINT64_MAX);
    }
    inline bool operator==(const b128_neon &other) const {
        uint32x4_t eq = vreinterpretq_u32_u64(vceqq_u64(v, other.v));
        return vminvq_u32(eq) == UINT32_MAX;
    }
    inline b128_neon &operator^=(const b128_neon &other) {
        v = veorq_u64(v, other.v);
        return *this;
    }
    inline b128_neon &operator&=(const b128_neon &other) {
        v = vandq_u64(v, other.v);
        return *this;
    }
    inline b128_neon &operator|=(const b128_neon &other) {
        v = vorrq_u64(v, other.v);
        return *this;
    }
    inline b128_neon operator~() const {
        return {veorq_u64(v, vdupq_n_u64(UINT64_MAX))};
    }
    inline b128_neon operator^(const b128_neon &other) const {
        return {veorq_u64(v, other.v)};
    }
    inline b128_neon operator&(const b128_neon &other) const {
        return {vandq_u64(v, other.v)};
    }
    inline b128_neon operator|(const b128_neon &other) const {
        return {vorrq_u64(v, other.v)};
    }
    inline static b128_neon from_u64s(std::array<uint64_t, 2> vs) {
        return {vld1q_u64(vs.data())};
    }
    inline static b128_neon from_u32_broadcast(uint32_t v) {
        return {vreinterpretq_u64_u32(vdupq_n_u32(v))};
    }
    inline b128_neon &u64_iadd(const b128_neon &other) {
        v = vaddq_u64(v, other.v);
        return *this;
    }
    inline b128_neon u64_add(const b128_neon &other) const {
        return {vaddq_u64(v, other.v)};
    }
    inline b128_neon u64_right_shift(size_t shift) const {
        return {vshlq_u64(v, vdupq_n_s64(-static_cast<int64_t>(shift)))};
    }
    inline b128_neon u64_left_shift(size_t shift) const {
        return {vshlq_u64(v, vdupq_n_s64(static_cast<int64_t>(shift)))};
    }
    inline b128_neon u32_add(const b128_neon &other) const {
        return {vreinterpretq_u64_u32(vaddq_u32(vreinterpretq_u32_u64(v), vreinterpretq_u32_u64(other.v)))};
    }
    inline b128_neon u32_right_shift(size_t shift) const {
        return {vreinterpretq_u64_u32(vshlq_u32(vreinterpretq_u32_u64(v), vdupq_n_s32(-static_cast<int32_t>(shift))))};
    }
    inline b128_neon u32_left_shift(size_t shift) const {
        return {vreinterpretq_u64_u32(vshlq_u32(vreinterpretq_u32_u64(v), vdupq_n_s32(static_cast<int32_t>(shift))))};
    }
    inline b128_neon u32_eq(const b128_neon &other) const {
        return {vreinterpretq_u64_u32(vceqq_u32(vreinterpretq_u32_u64(v), vreinterpretq_u32_u64(other.v)))};
    }
};
inline std::ostream &operator<<(std::ostream &out, const b128_neon &v) {
    for (size_t k = 0; k < sizeof(v) * 8; k++) {
        out << "_1"[v.bit(k)];
    }
    return out;
}
#endif

}  // namespace kickmix

#endif
