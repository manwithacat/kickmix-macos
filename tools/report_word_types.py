"""Print which SIMD word types the vendored source will select on this host (static check)."""
import platform
m = platform.machine().lower()
if m in ("arm64", "aarch64"):
    print("AArch64: b128 = b128_neon, b256 = b256_neon, b512 = b512_polyfill (unless KICKMIX_DISABLE_NEON)")
elif "x86" in m or "amd64" in m:
    print("x86-64: b128 = b128_sse, b256 = b256_avx if -mavx else polyfill, b512 = AVX-512 if enabled")
else:
    print(f"{m}: portable polyfill word types")
