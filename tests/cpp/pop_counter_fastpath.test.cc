// Equivalence test for patches/popcounter-fastpath.patch.
//
// Compares the vendored PopCounter (with the all-ones fast path) against a verbatim copy
// of the upstream implementation, for every word type the build supports, using random
// sequences of all-ones, all-zero and partial masks. Totals are compared at every
// position after every call, and the call counts cross the 31/32/33 and 63/64/65
// boundaries of the `step` transfer schedule.
//
// This file was written with the assistance of an AI coding model (Claude) under the
// repository owner's direction; see NOTICE. It is licensed under Apache-2.0.
//
// Build: tools/cpp_word_type_tests.sh (no Bazel needed).

#include <cstdint>
#include <random>
#include <vector>

#include "gtest/gtest.h"

#include "kickmix/simd/simd.test.h"
#include "kickmix/util/pop_counter.h"
#include "test.test.h"

using namespace kickmix;

namespace {

/// Verbatim copy of upstream's PopCounter::masked_increments (commit 638777fa), without
/// the fast path. Reference for the equivalence checks below.
template <typename TWord>
struct ReferencePopCounter {
    constexpr static size_t BATCH_SIZE = sizeof(TWord) * 8;
    TWord bit_totals[6]{};
    TWord paired_totals[32]{};
    uint32_t shared_count = 0;
    uint8_t step = 0;

    inline uint64_t compute_total(size_t k) const {
        uint64_t result = static_cast<uint64_t>(paired_totals[k % 32].u32(k / 32)) << 5;
        for (size_t r = 0; r < 6; r++) {
            result += static_cast<uint64_t>(bit_totals[r].bit(k)) << r;
        }
        return result + shared_count;
    }

    inline void masked_increments(const TWord &mask_ref) {
        TWord mask = mask_ref;
        step++;
        step &= 31;
        paired_totals[step].u64_iadd(bit_totals[5].u64_right_shift(step) & TWord::from_u32_broadcast(1));
        bit_totals[5] &= ~(TWord::from_u32_broadcast(1).u64_left_shift(step));
        bit_totals[0] ^= mask;
        mask &= ~bit_totals[0];
        bit_totals[1] ^= mask;
        mask &= ~bit_totals[1];
        bit_totals[2] ^= mask;
        mask &= ~bit_totals[2];
        bit_totals[3] ^= mask;
        mask &= ~bit_totals[3];
        bit_totals[4] ^= mask;
        mask &= ~bit_totals[4];
        bit_totals[5] ^= mask;
    }
};

enum class Kind { ALL_ONES, ZERO, RANDOM, NEAR_ALL_ONES, SINGLE_ZERO_BIT };

template <typename W>
W make_mask(Kind kind, std::mt19937_64 &rng) {
    switch (kind) {
        case Kind::ALL_ONES:
            return ~W{};
        case Kind::ZERO:
            return W{};
        case Kind::RANDOM:
            return W::random(rng);
        case Kind::NEAR_ALL_ONES: {
            // All ones except for one random position (the fast path must not trigger).
            W w = ~W{};
            w.set_bit(rng() % (sizeof(W) * 8), false);
            return w;
        }
        case Kind::SINGLE_ZERO_BIT: {
            W w{};
            w.set_bit(rng() % (sizeof(W) * 8), true);
            return w;
        }
    }
    return W{};
}

template <typename W>
void compare_all(const PopCounter<W> &a, const ReferencePopCounter<W> &b, const char *what, size_t call) {
    for (size_t k = 0; k < PopCounter<W>::BATCH_SIZE; k++) {
        ASSERT_EQ(a.compute_total(k), b.compute_total(k)) << what << " call " << call << " position " << k;
    }
}

template <typename W>
void run_sequence(const std::vector<Kind> &kinds, size_t calls, std::mt19937_64 &rng, const char *what) {
    PopCounter<W> fast;
    ReferencePopCounter<W> ref;
    for (size_t i = 0; i < calls; i++) {
        W m = make_mask<W>(kinds[rng() % kinds.size()], rng);
        fast.masked_increments(m);
        ref.masked_increments(m);
        if (i < 140 || i % 97 == 0) {
            compare_all(fast, ref, what, i);
            if (::testing::Test::HasFatalFailure()) return;
        }
    }
    compare_all(fast, ref, what, calls);
}

}  // namespace

TEST_EACH_SIZE_W(pop_counter_fastpath, boundaries, {
    std::mt19937_64 rng = INDEPENDENT_TEST_RNG();
    // Every prefix length around the step-cycle boundaries, with a partial mask after the
    // all-ones run (so the stripes are exercised both before and after the shared counts).
    for (size_t ones : {0, 1, 2, 30, 31, 32, 33, 62, 63, 64, 65, 66, 127, 128, 129}) {
        for (size_t partial : {0, 1, 31, 32, 33, 63, 64, 65}) {
            PopCounter<W> fast;
            ReferencePopCounter<W> ref;
            for (size_t i = 0; i < partial; i++) {
                W m = W::random(rng);
                fast.masked_increments(m);
                ref.masked_increments(m);
            }
            for (size_t i = 0; i < ones; i++) {
                fast.masked_increments(~W{});
                ref.masked_increments(~W{});
            }
            for (size_t i = 0; i < partial; i++) {
                W m = W::random(rng);
                fast.masked_increments(m);
                ref.masked_increments(m);
            }
            compare_all(fast, ref, "boundary", ones * 1000 + partial);
            if (HasFatalFailure()) return;
        }
    }
})

TEST_EACH_SIZE_W(pop_counter_fastpath, all_ones_only, {
    PopCounter<W> fast;
    ReferencePopCounter<W> ref;
    for (size_t i = 0; i < 200; i++) {
        fast.masked_increments(~W{});
        ref.masked_increments(~W{});
        compare_all(fast, ref, "all_ones_only", i);
        if (HasFatalFailure()) return;
    }
})

TEST_EACH_SIZE_W(pop_counter_fastpath, zero_only, {
    PopCounter<W> fast;
    ReferencePopCounter<W> ref;
    for (size_t i = 0; i < 200; i++) {
        fast.masked_increments(W{});
        ref.masked_increments(W{});
    }
    compare_all(fast, ref, "zero_only", 200);
})

TEST_EACH_SIZE_W(pop_counter_fastpath, randomized_mixed, {
    std::mt19937_64 rng = INDEPENDENT_TEST_RNG();
    // 100 independent trials per word type.
    for (size_t trial = 0; trial < 100; trial++) {
        std::vector<Kind> kinds{Kind::ALL_ONES, Kind::ALL_ONES, Kind::ZERO, Kind::RANDOM, Kind::NEAR_ALL_ONES,
                                Kind::SINGLE_ZERO_BIT};
        size_t calls = 1 + rng() % 400;
        run_sequence<W>(kinds, calls, rng, "mixed");
        if (HasFatalFailure()) return;
    }
})

TEST_EACH_SIZE_W(pop_counter_fastpath, mostly_all_ones, {
    std::mt19937_64 rng = INDEPENDENT_TEST_RNG();
    for (size_t trial = 0; trial < 20; trial++) {
        std::vector<Kind> kinds{Kind::ALL_ONES, Kind::ALL_ONES, Kind::ALL_ONES, Kind::ALL_ONES, Kind::ALL_ONES,
                                Kind::ALL_ONES, Kind::ALL_ONES, Kind::RANDOM};
        run_sequence<W>(kinds, 2000, rng, "mostly_all_ones");
        if (HasFatalFailure()) return;
    }
})
