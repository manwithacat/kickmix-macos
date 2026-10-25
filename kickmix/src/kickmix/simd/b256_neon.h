#ifndef KICKMIX_SIMD_256_NEON_H
#define KICKMIX_SIMD_256_NEON_H

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
/// 256-bit word backed by two AArch64 NEON registers.
///
/// AArch64 has no 256-bit vector register, so this type pairs two 128-bit registers and
/// applies every operation to both. `v[0]` holds the lowest 128 bits, matching the lane
/// order of `b256_avx` and `b256_polyfill`.
struct alignas(sizeof(uint64_t) * 4) b256_neon {
    uint64x2_t v[2];

    constexpr static size_t BIT_SIZE = 256;
    constexpr static const char *NAME = "b256_neon";
    bool non_zero() const {
        return vmaxvq_u32(vreinterpretq_u32_u64(vorrq_u64(v[0], v[1]))) != 0;
    }
    static b256_neon random(std::mt19937_64 &rng) {
        b256_neon result;
        result.randomize(rng);
        return result;
    }
    void randomize(std::mt19937_64 &rng) {
        uint64_t words[4];
        for (size_t k = 0; k < 4; k++) {
            words[k] = rng();
        }
        v[0] = vld1q_u64(words);
        v[1] = vld1q_u64(words + 2);
    }
    inline uint64_t u64(size_t k) const {
        uint64_t words[4];
        memcpy(words, v, sizeof(words));
        return words[k];
    }
    inline uint32_t u32(size_t k) const {
        uint32_t words[8];
        memcpy(words, v, sizeof(words));
        return words[k];
    }
    inline bool bit(size_t k) const {
        return (u64(k / 64) >> (k % 64)) & 1;
    }
    inline void set_bit(size_t k, bool new_value) {
        uint64_t words[4];
        memcpy(words, v, sizeof(words));
        uint64_t mask = uint64_t{1} << (k % 64);
        words[k / 64] = (words[k / 64] & ~mask) | ((uint64_t)new_value << (k % 64));
        v[0] = vld1q_u64(words);
        v[1] = vld1q_u64(words + 2);
    }
    inline void clear_to_zero() {
        v[0] = vdupq_n_u64(0);
        v[1] = vdupq_n_u64(0);
    }
    inline void clear_to_max() {
        v[0] = vdupq_n_u64(UINT64_MAX);
        v[1] = vdupq_n_u64(UINT64_MAX);
    }
    inline bool operator==(const b256_neon &other) const {
        uint64x2_t eq = vandq_u64(vceqq_u64(v[0], other.v[0]), vceqq_u64(v[1], other.v[1]));
        return vminvq_u32(vreinterpretq_u32_u64(eq)) == UINT32_MAX;
    }
    inline b256_neon &operator^=(const b256_neon &other) {
        v[0] = veorq_u64(v[0], other.v[0]);
        v[1] = veorq_u64(v[1], other.v[1]);
        return *this;
    }
    inline b256_neon &operator&=(const b256_neon &other) {
        v[0] = vandq_u64(v[0], other.v[0]);
        v[1] = vandq_u64(v[1], other.v[1]);
        return *this;
    }
    inline b256_neon &operator|=(const b256_neon &other) {
        v[0] = vorrq_u64(v[0], other.v[0]);
        v[1] = vorrq_u64(v[1], other.v[1]);
        return *this;
    }
    inline b256_neon operator~() const {
        uint64x2_t ones = vdupq_n_u64(UINT64_MAX);
        return {{veorq_u64(v[0], ones), veorq_u64(v[1], ones)}};
    }
    inline b256_neon operator^(const b256_neon &other) const {
        return {{veorq_u64(v[0], other.v[0]), veorq_u64(v[1], other.v[1])}};
    }
    inline b256_neon operator&(const b256_neon &other) const {
        return {{vandq_u64(v[0], other.v[0]), vandq_u64(v[1], other.v[1])}};
    }
    inline b256_neon operator|(const b256_neon &other) const {
        return {{vorrq_u64(v[0], other.v[0]), vorrq_u64(v[1], other.v[1])}};
    }
    inline static b256_neon from_u64s(std::array<uint64_t, 4> vs) {
        return {{vld1q_u64(vs.data()), vld1q_u64(vs.data() + 2)}};
    }
    inline static b256_neon from_u32_broadcast(uint32_t v) {
        uint64x2_t word = vreinterpretq_u64_u32(vdupq_n_u32(v));
        return {{word, word}};
    }
    inline b256_neon &u64_iadd(const b256_neon &other) {
        v[0] = vaddq_u64(v[0], other.v[0]);
        v[1] = vaddq_u64(v[1], other.v[1]);
        return *this;
    }
    inline b256_neon u64_add(const b256_neon &other) const {
        return {{vaddq_u64(v[0], other.v[0]), vaddq_u64(v[1], other.v[1])}};
    }
    inline b256_neon u64_right_shift(size_t shift) const {
        int64x2_t count = vdupq_n_s64(-static_cast<int64_t>(shift));
        return {{vshlq_u64(v[0], count), vshlq_u64(v[1], count)}};
    }
    inline b256_neon u64_left_shift(size_t shift) const {
        int64x2_t count = vdupq_n_s64(static_cast<int64_t>(shift));
        return {{vshlq_u64(v[0], count), vshlq_u64(v[1], count)}};
    }
    inline b256_neon u32_add(const b256_neon &other) const {
        return {
            {vreinterpretq_u64_u32(vaddq_u32(vreinterpretq_u32_u64(v[0]), vreinterpretq_u32_u64(other.v[0]))),
             vreinterpretq_u64_u32(vaddq_u32(vreinterpretq_u32_u64(v[1]), vreinterpretq_u32_u64(other.v[1])))}};
    }
    inline b256_neon u32_right_shift(size_t shift) const {
        int32x4_t count = vdupq_n_s32(-static_cast<int32_t>(shift));
        return {
            {vreinterpretq_u64_u32(vshlq_u32(vreinterpretq_u32_u64(v[0]), count)),
             vreinterpretq_u64_u32(vshlq_u32(vreinterpretq_u32_u64(v[1]), count))}};
    }
    inline b256_neon u32_left_shift(size_t shift) const {
        int32x4_t count = vdupq_n_s32(static_cast<int32_t>(shift));
        return {
            {vreinterpretq_u64_u32(vshlq_u32(vreinterpretq_u32_u64(v[0]), count)),
             vreinterpretq_u64_u32(vshlq_u32(vreinterpretq_u32_u64(v[1]), count))}};
    }
    inline b256_neon u32_eq(const b256_neon &other) const {
        return {
            {vreinterpretq_u64_u32(vceqq_u32(vreinterpretq_u32_u64(v[0]), vreinterpretq_u32_u64(other.v[0]))),
             vreinterpretq_u64_u32(vceqq_u32(vreinterpretq_u32_u64(v[1]), vreinterpretq_u32_u64(other.v[1])))}};
    }
};
inline std::ostream &operator<<(std::ostream &out, const b256_neon &v) {
    for (size_t k = 0; k < sizeof(v) * 8; k++) {
        out << "_1"[v.bit(k)];
    }
    return out;
}
#endif

}  // namespace kickmix

#endif
