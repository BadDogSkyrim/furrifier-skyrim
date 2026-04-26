// Thin C wrapper around bc7enc that compresses a whole RGBA image into
// a contiguous BC7 block buffer in one call. Lets Python drive the
// encoder via ctypes without paying per-block FFI overhead — 32k blocks
// per 2048² face-tint, so the per-call cost would otherwise dominate.
//
// Build (MSVC):
//   cl /O2 /LD bc7enc.c bc7enc_wrapper.c /Febc7enc_wrapper.dll
//
// Build (gcc / clang):
//   gcc -O3 -shared -fPIC -o libbc7enc_wrapper.so \
//       bc7enc.c bc7enc_wrapper.c
//
// Inputs are a tightly-packed RGBA8 image (no row stride). Width and
// height MUST be multiples of 4 — the BC7 spec is block-based and we
// don't pad here. The caller supplies an output buffer of exactly
// (width / 4) * (height / 4) * 16 bytes.
//
// Returns 0 on success, non-zero on usage error.

#include <stdint.h>
#include <string.h>
#include "bc7enc.h"

#ifdef _WIN32
  #define EXPORT __declspec(dllexport)
#else
  #define EXPORT __attribute__((visibility("default")))
#endif


// One-shot init — bc7enc_compress_block_init() must be called before
// any compress_block call (per the upstream comment) but is idempotent.
// Calling it from compress_image keeps callers from forgetting.
static int g_initialized = 0;


EXPORT int bc7enc_compress_image_rgba(
    const uint8_t *rgba,
    int width, int height,
    uint8_t *out_blocks,
    int uber_level,
    int max_partitions,
    int perceptual)
{
    if (!rgba || !out_blocks) return 1;
    if (width <= 0 || height <= 0) return 2;
    if ((width & 3) || (height & 3)) return 3;  // must be multiple of 4

    if (!g_initialized) {
        bc7enc_compress_block_init();
        g_initialized = 1;
    }

    bc7enc_compress_block_params params;
    bc7enc_compress_block_params_init(&params);
    if (perceptual)
        bc7enc_compress_block_params_init_perceptual_weights(&params);
    else
        bc7enc_compress_block_params_init_linear_weights(&params);

    if (uber_level < 0) uber_level = 0;
    if (uber_level > BC7ENC_MAX_UBER_LEVEL) uber_level = BC7ENC_MAX_UBER_LEVEL;
    params.m_uber_level = (uint32_t)uber_level;

    if (max_partitions < 0) max_partitions = 0;
    if (max_partitions > BC7ENC_MAX_PARTITIONS1) max_partitions = BC7ENC_MAX_PARTITIONS1;
    params.m_max_partitions_mode = (uint32_t)max_partitions;

    int blocks_x = width / 4;
    int blocks_y = height / 4;

    // Per-block: gather 16 RGBA pixels (4 rows × 4 cols) from the source
    // image into a contiguous 64-byte buffer, then call the encoder.
    uint8_t block[64];
    uint8_t *out = out_blocks;

    for (int by = 0; by < blocks_y; by++) {
        for (int bx = 0; bx < blocks_x; bx++) {
            for (int row = 0; row < 4; row++) {
                const uint8_t *src = rgba + ((by * 4 + row) * width + bx * 4) * 4;
                memcpy(block + row * 16, src, 16);
            }
            bc7enc_compress_block(out, block, &params);
            out += BC7ENC_BLOCK_SIZE;
        }
    }
    return 0;
}
