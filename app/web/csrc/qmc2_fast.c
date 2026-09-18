#include <stdlib.h>
#include <string.h>
#include <math.h>

/* MapCipher for keys <= 300 bytes */
static unsigned char map_rotate(unsigned char value, int bits) {
    int shift = (bits + 4) % 8;
    return ((value << shift) | (value >> shift)) & 0xFF;
}

static unsigned char map_get_mask(const unsigned char *key, int n, int offset) {
    if (n == 0) return 0;
    if (offset > 0x7FFF) offset %= 0x7FFF;
    int idx = (offset * offset + 71214) % n;
    return map_rotate(key[idx], idx & 0x07);
}

void map_decrypt(const unsigned char *key, int key_len, unsigned char *buf, int buf_len, int offset) {
    for (int i = 0; i < buf_len; i++) {
        buf[i] ^= map_get_mask(key, key_len, offset + i);
    }
}

/* RC4Cipher for keys > 300 bytes */
static unsigned int rc4_compute_hash(const unsigned char *key, int n) {
    unsigned int h = 1;
    for (int i = 0; i < n; i++) {
        if (key[i] == 0) continue;
        unsigned int nh = h * (unsigned int)key[i];
        if (nh == 0 || nh <= h) break;
        h = nh;
    }
    return h;
}

static int rc4_get_segment_skip(const unsigned char *key, int n, unsigned int hash, int id_val) {
    long long seed = (long long)key[id_val % n];
    if (seed == 0) return 0;
    long long idx = (long long)((double)hash / (double)((id_val + 1) * seed) * 100.0);
    return (int)(idx % n);
}

void rc4_decrypt(const unsigned char *key, int key_len, unsigned char *buf, int buf_len, int offset) {
    int n = key_len;
    int *box = (int *)malloc(n * sizeof(int));
    for (int i = 0; i < n; i++) box[i] = i & 0xFF;
    int j = 0;
    for (int i = 0; i < n; i++) {
        j = (j + box[i] + key[i]) % n;
        int tmp = box[i]; box[i] = box[j]; box[j] = tmp;
    }
    unsigned int hash = rc4_compute_hash(key, n);

    int FIRST_SEG = 128;
    int SEG_SIZE = 5120;
    int processed = 0;
    int pos = offset;

    if (pos < FIRST_SEG) {
        int block = buf_len < (FIRST_SEG - pos) ? buf_len : (FIRST_SEG - pos);
        for (int i = 0; i < block; i++) {
            int skip = rc4_get_segment_skip(key, n, hash, pos + i);
            buf[processed + i] ^= key[skip];
        }
        processed += block;
        pos += block;
    }

    while (processed < buf_len) {
        int seg_offset = pos % SEG_SIZE;
        int block = SEG_SIZE - seg_offset;
        if (block > buf_len - processed) block = buf_len - processed;

        int *box_copy = (int *)malloc(n * sizeof(int));
        memcpy(box_copy, box, n * sizeof(int));
        int jj = 0, kk = 0;
        int skip_len = seg_offset + rc4_get_segment_skip(key, n, hash, pos / SEG_SIZE);

        for (int i = -skip_len; i < block; i++) {
            jj = (jj + 1) % n;
            kk = (box_copy[jj] + kk) % n;
            int tmp = box_copy[jj]; box_copy[jj] = box_copy[kk]; box_copy[kk] = tmp;
            if (i >= 0) {
                int idx = (box_copy[jj] + box_copy[kk]) % n;
                buf[processed + i] ^= box_copy[idx];
            }
        }
        free(box_copy);
        processed += block;
        pos += block;
    }
    free(box);
}

/* Unified decrypt: picks cipher based on key length */
void qmc2_decrypt(const unsigned char *key, int key_len, unsigned char *buf, int buf_len, int offset) {
    if (key_len > 300) {
        rc4_decrypt(key, key_len, buf, buf_len, offset);
    } else {
        map_decrypt(key, key_len, buf, buf_len, offset);
    }
}
