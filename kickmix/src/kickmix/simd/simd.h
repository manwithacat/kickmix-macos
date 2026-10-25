#ifndef KICKMIX_SIMD_H
#define KICKMIX_SIMD_H

#include "b128_neon.h"
#include "b128_polyfill.h"
#include "b128_sse.h"
#include "b256_avx.h"
#include "b256_neon.h"
#include "b256_polyfill.h"
#include "b512_avx.h"
#include "b512_polyfill.h"
#include "b64_polyfill.h"

namespace kickmix {

using b64 = b64_polyfill;

#ifdef __SSE__
using b128 = b128_sse;
#elif defined(__aarch64__) && defined(__ARM_NEON) && !defined(KICKMIX_DISABLE_NEON)
using b128 = b128_neon;
#else
using b128 = b128_polyfill;
#endif

#ifdef __AVX__
using b256 = b256_avx;
#elif defined(__aarch64__) && defined(__ARM_NEON) && !defined(KICKMIX_DISABLE_NEON)
using b256 = b256_neon;
#else
using b256 = b256_polyfill;
#endif

#ifdef __AVX512F__
using b512 = b512_avx;
#else
using b512 = b512_polyfill;
#endif

}  // namespace kickmix

#endif
