/**
 * WordSwift — word search engine derived from fastText.
 *
 * Author: Supreet Sethi <supreet.sethi@gmail.com>
 * Repository: https://github.com/djinn/WordSwift
 * LinkedIn: https://www.linkedin.com/in/djinn
 *
 * Based on fastText by Facebook, Inc.
 * Licensed under MIT License.
 */

#pragma once
#define _POSIX_C_SOURCE 200112L

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>

/* ------------------------------------------------------------------ */
/* Error codes */
/* ------------------------------------------------------------------ */
typedef enum {
    WS_OK = 0,
    WS_ERROR_ALLOCATION,
    WS_ERROR_IO,
    WS_ERROR_INVALID_ARGUMENT,
    WS_ERROR_NAN,
    WS_ERROR_MODEL_VERSION,
    WS_ERROR_QUANTIZATION,
    WS_ERROR_LEVELDB,
    WS_ERROR_NOT_FOUND
} ws_error_code;

const char* ws_strerror(ws_error_code err);

/* ------------------------------------------------------------------ */
/* Floating point type */
/* ------------------------------------------------------------------ */
typedef float ws_real;

/* ------------------------------------------------------------------ */
/* Prediction result */
/* ------------------------------------------------------------------ */
typedef struct {
    ws_real score;
    int32_t label_id;
    char*   word;
} ws_pred_entry;

/* ------------------------------------------------------------------ */
/* Dictionary entry */
/* ------------------------------------------------------------------ */
typedef enum {
    WS_ENTRY_WORD = 0,
    WS_ENTRY_LABEL = 1
} ws_entry_type;

typedef struct {
    char*    word;
    int64_t  count;
    ws_entry_type type;
} ws_dict_entry;

/* ------------------------------------------------------------------ */
/* Dictionary / Vocabulary */
/* ------------------------------------------------------------------ */
typedef struct {
    uint32_t hash_seed;
    uint32_t bucket_hashes;
    size_t   vocab_size;
    size_t   max_vocab;
    ws_dict_entry** entries;
    int32_t*        sorted_indices;
    size_t          n_sorted;
    int32_t min_n;
    int32_t max_n;
    int32_t bucket;
    double  discard_threshold;
    int32_t* lookup_buckets;
    int32_t* lookup_next;
} ws_dictionary;

/* ------------------------------------------------------------------ */
/* Matrix */
/* ------------------------------------------------------------------ */
typedef struct {
    ws_real* data;
    size_t   m;          /* rows */
    size_t   n;          /* cols */
} ws_matrix;

/* ------------------------------------------------------------------ */
/* LevelDB (opaque) */
/* ------------------------------------------------------------------ */
typedef struct ws_leveldb ws_leveldb;

/* ------------------------------------------------------------------ */
/* WordSwift engine */
/* ------------------------------------------------------------------ */
typedef struct {
    /* Core fastText components */
    ws_dictionary* dict;
    ws_matrix*     input_matrix;
    ws_matrix*     output_matrix;

    /* Training params */
    int32_t dim;
    int     ws;
    int     epoch;
    int     min_count;
    int     neg;
    int     word_ngrams;
    double  lr;
    double  t;
    int     bucket;
    int     minn;
    int     maxn;
    uint32_t seed;
    char*   labe_prefix;

    /* LevelDB index handle */
    ws_leveldb* ldb;
    char*       ldb_path;
} ws_engine;

/* ------------------------------------------------------------------ */
/* API Functions */
/* ------------------------------------------------------------------ */

ws_error_code ws_engine_new(ws_engine** out);
void     ws_engine_free(ws_engine* ws);

ws_error_code ws_engine_train_unsupervised(ws_engine* ws, const char* corpus_path,
                                           int32_t dim, int ws_val, int epoch,
                                           int min_count, double lr,
                                           int model_type,
                                           void (*cb)(double,double,double,double,int64_t,void*),
                                           void* cb_data);

ws_error_code ws_engine_save(ws_engine* ws, const char* path);
ws_error_code ws_engine_load(ws_engine* ws, const char* path);
ws_error_code ws_engine_save_vectors(ws_engine* ws, const char* path);

ws_error_code ws_engine_get_word_vector(ws_engine* ws, const char* word, ws_real* vec_out);
ws_error_code ws_engine_get_sentence_vector(ws_engine* ws, const char* text, ws_real* vec_out);

ws_error_code ws_engine_get_nearest(ws_engine* ws, const char* word, int n,
                                    ws_pred_entry* results, size_t cap, size_t* out_count);
ws_error_code ws_engine_get_analogies(ws_engine* ws, const char* a, const char* b,
                                      const char* c, int n, ws_pred_entry* results,
                                      size_t cap, size_t* out_count);

ws_error_code ws_engine_set_leveldb(ws_engine* ws, const char* db_path);
void          ws_engine_gyro(ws_engine* ws);
void          ws_engine_close_leveldb(ws_engine* ws);

ws_error_code ws_engine_index_word(ws_engine* ws, const char* word);
ws_error_code ws_engine_index_all(ws_engine* ws);

ws_error_code ws_engine_search_index(ws_engine* ws, const char* query_word, int n,
                                     ws_pred_entry* results, size_t cap,
                                     size_t* out_count, double min_score);

size_t    ws_engine_vocab_size(ws_engine* ws);
int32_t   ws_engine_get_dim(ws_engine* ws);
size_t    ws_engine_index_size(ws_engine* ws);
const char* ws_engine_word_at(ws_engine* ws, size_t idx);

void ws_engine_free_results(ws_pred_entry* results, size_t n);