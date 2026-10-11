/* lhs_tts.h — ABI for the L&H/RealSpeak TTS API as shipped by Dragon 16 (TTS.cab).
 *
 * Reconstructed from the PE export tables of lhtts01.dll / lhsp01.dll and from the
 * disassembly of the consumers (natspeak.exe).  See SPEC_TTS.md §§3–4.
 *
 * Original binaries are 32-bit (i386), stdcall; names below are the undecorated exports.
 * Decorations: @N = argument bytes (e.g. lhs_ttsOpenW@16).
 */
#ifndef LHS_TTS_H
#define LHS_TTS_H

#include <stdint.h>
#include <wchar.h>

#ifdef __cplusplus
extern "C" {
#endif

#if defined(_WIN32)
#  define LHS_CALL __stdcall
#else
#  define LHS_CALL
#endif

/* ------------------------------------------------------------------ error codes */
typedef enum {
    LHS_OK                  = 0,
    LHS_E_GENERAL           = 1,
    LHS_E_OPENFAIL          = 3,
    LHS_E_WRONGLANG         = 4,
    LHS_E_NOVOICE           = 5,
    LHS_E_MEMALLOC          = 6,
    LHS_E_TTS_INSTBUSY      = 8,
    LHS_E_INVALIDHANDLE     = 0xB,
    LHS_E_INVALIDARG        = 0xC,
    LHS_E_NULLPOINTER       = 0xD,
    LHS_E_BUFFERTOOSMALL    = 0xE,
    LHS_E_CONVERSIONFAILED  = 0x10,
    LHS_E_BADCOMMAND        = 0x17,
    LHS_E_TTS_NOLANGUAGE    = 0x80250000u
} lhs_error_t;

/* ------------------------------------------------------------------ parameters */
#define LHS_PARAM_LANGUAGE   0x400u   /* wide string, e.g. L"American English"  */
#define LHS_PARAM_OUTPUTCFG  0x2000u  /* numeric; 0x202 observed                 */

/* Parameter entry.  The original passes a fixed-stride record of 0x21 dwords (132 bytes);
 * only the first (state/type) and second (id) fields are used by the API layer, the value
 * follows according to the parameter id.  This 132-byte form reproduces the observed stride. */
typedef struct {
    uint32_t state;          /* 2 | 8 | 0x10 observed ("instance busy" guards) */
    uint32_t id;
    uint32_t value_u32;
    const wchar_t *value_wstr;
    uint8_t  reserved[132 - 4 * 4];
} lhs_param_t;

/* Output-device callback descriptor used by lhs_ttsSetOutDevice. */
typedef int (LHS_CALL *lhs_out_fn)(const void *pcm, uint32_t bytes, void *cookie);

typedef struct {
    lhs_out_fn fn;
    void      *cookie;
} lhs_out_device_t;

/* ------------------------------------------------------------------ lifecycle */
int LHS_CALL lhs_InitializeEx(uint32_t flags, const char *key, int count, void **out);
int LHS_CALL lhs_Initialize(uint32_t flags, void **out);
int LHS_CALL lhs_UnInitialize(void *h);
const wchar_t *LHS_CALL lhs_ErrorGetStringW(int code);
const char    *LHS_CALL lhs_ErrorGetStringA(int code);

/* ------------------------------------------------------------------ instances */
int LHS_CALL lhs_ttsOpenA(void *engine, const char *voice, void **inst, uint32_t flags,
                          uint32_t flags2);
int LHS_CALL lhs_ttsOpenW(void *engine, const wchar_t *voice, void **inst, uint32_t flags,
                          uint32_t flags2);
int LHS_CALL lhs_ttsClose(void *inst);

/* ------------------------------------------------------------------ speech */
int LHS_CALL lhs_ttsSetTextA(void *inst, const char *text, uint32_t bytes, void *unused);
int LHS_CALL lhs_ttsSetTextW(void *inst, const wchar_t *text, uint32_t bytes, void *unused);
int LHS_CALL lhs_ttsSpeak(void *inst, uint32_t mode);
int LHS_CALL lhs_ttsStop(void *inst);
int LHS_CALL lhs_ttsPause(void *inst);
int LHS_CALL lhs_ttsResume(void *inst);
int LHS_CALL lhs_ttsProcess(void *inst, uint32_t mode);

/* ------------------------------------------------------------------ parameters */
int LHS_CALL lhs_ttsSetParamListA(void *inst, lhs_param_t *list, uint32_t count);
int LHS_CALL lhs_ttsSetParamListW(void *inst, lhs_param_t *list, uint32_t count);
int LHS_CALL lhs_ttsGetParamListA(void *inst, lhs_param_t *list, uint32_t count);
int LHS_CALL lhs_ttsGetParamListW(void *inst, lhs_param_t *list, uint32_t count);

/* ------------------------------------------------------------------ discovery */
int LHS_CALL lhs_ttsGetVoiceListA(void *inst, void *out, uint32_t cch, uint32_t *count,
                                  uint32_t flags);
int LHS_CALL lhs_ttsGetVoiceListW(void *inst, void *out, uint32_t cch, uint32_t *count,
                                  uint32_t flags);
int LHS_CALL lhs_ttsGetPPListA(void *inst, void *out, uint32_t cch, uint32_t *count,
                               uint32_t flags);
int LHS_CALL lhs_ttsGetPPListW(void *inst, void *out, uint32_t cch, uint32_t *count,
                               uint32_t flags);
int LHS_CALL lhs_ttsSetDictionaries(void *inst, void *list, uint32_t count);

/* ------------------------------------------------------------------ devices */
int LHS_CALL lhs_ttsSetInDevice(void *inst, void *desc);
int LHS_CALL lhs_ttsSetOutDevice(void *inst, lhs_out_device_t *desc);

/* ------------------------------------------------------------------ output */
int LHS_CALL lhs_ttsGenOutputFileA(void *inst, const char *path, uint32_t fmt, uint32_t flags);
int LHS_CALL lhs_ttsGenOutputFileW(void *inst, const wchar_t *path, uint32_t fmt, uint32_t flags);
int LHS_CALL lhs_ttsGenPhoneticStringA(void *inst, const char *text, char *out, uint32_t cch,
                                       uint32_t flags);
int LHS_CALL lhs_ttsGenPhoneticStringW(void *inst, const wchar_t *text, wchar_t *out, uint32_t cch,
                                       uint32_t flags);

/* ------------------------------------------------------------------ status/misc */
int LHS_CALL lhs_ttsGetInstStatus(void *inst, uint32_t *status);
int LHS_CALL lhs_ttsGetLipSyncInfo(void *inst, void *info, uint32_t bytes);
int LHS_CALL lhs_ttsLoadRuleTableA(void *inst, const char *path, uint32_t flags);
int LHS_CALL lhs_ttsLoadRuleTableW(void *inst, const wchar_t *path, uint32_t flags);
int LHS_CALL lhs_ttsUnLoadRuleTable(void *inst, void *table);
int LHS_CALL lhs_ttsUnLoadAllRuleTables(void *inst);
int LHS_CALL lhs_ttsDetectLanguageA(void *inst, const char *text, uint32_t bytes, uint32_t *lang,
                                    uint32_t flags, void *reserved);
int LHS_CALL lhs_ttsDetectLanguageW(void *inst, const wchar_t *text, uint32_t bytes,
                                    uint32_t *lang, uint32_t flags, void *reserved);
int LHS_CALL lhs_ttsForward(void *inst, uint32_t n, uint32_t units);
int LHS_CALL lhs_ttsBackward(void *inst, uint32_t n, uint32_t units);

/* ------------------------------------------------------------------ engine core
 * `rsstdv2_core.dll` exports the entry point that returns the `ttscore` interface table;
 * `brk_InterfaceQuery(engine, "ttscore", ...)` performs the equivalent lookup.
 *
 * Slot offsets below are the ones actually called by lhtts01.dll (verified).  A core object
 * compatible with the shipped API layer must expose at least these:
 *
 *   +0x1c  int   (*close)(uint32_t hi, uint32_t lo);                     /* CloseCore    */
 *   +0x24  int   (*open)(uint64_t *handle, const void *open_args);       /* CoreOpen     */
 *   +0x28  int   (*set_callbacks)(void *ctx, void *cb1, void *cb2);      /* error/notify */
 *   +0x2c  void  (*release)(void);                                       /* final release*/
 *   +0x48  int   (*set_param)(uint32_t hi, uint32_t lo, uint32_t value, void *ptr);
 *   +0x54  int   (*set_text)(uint32_t hi, uint32_t lo, const void *buf, uint32_t len);
 */
typedef struct lhs_tts_core_vtbl {
    void *slots_00_18[7];                 /* vtable prefix not exercised by the API layer */
    int  (LHS_CALL *close)(uint32_t hi, uint32_t lo);
    int  (LHS_CALL *open)(uint64_t *handle, const void *args);
    int  (LHS_CALL *set_callbacks)(void *ctx, void *cb1, void *cb2);
    void (LHS_CALL *release)(void);
    void *slots_30_44[6];
    int  (LHS_CALL *set_param)(uint32_t hi, uint32_t lo, uint32_t value, void *ptr);
    int  (LHS_CALL *set_text)(uint32_t hi, uint32_t lo, const void *buf, uint32_t len);
} lhs_tts_core_vtbl_t;

/* Core* exports of rsstdv2_core.dll (for reference; the interface table is preferred). */
void *CoreGetInterface(const char *name);
int   CoreInitialize(void);
int   CoreUninitialize(void);
int   CoreOpen(uint64_t *handle, const void *args);
int   CoreClose(uint32_t hi, uint32_t lo);
int   CoreSetText(uint32_t hi, uint32_t lo, const void *buf, uint32_t len);
int   CoreForward(uint32_t hi, uint32_t lo, uint32_t n);
int   CoreBackward(uint32_t hi, uint32_t lo, uint32_t n);
int   CoreReset(uint64_t handle);
int   CoreStop(uint64_t handle);
int   CoreTranscribe(uint64_t handle, const void *args);
int   CoreGetPcmBuf(uint64_t handle, void *buf, uint32_t *len, void *info);
int   CoreGetParam(uint64_t handle, uint32_t id, void *value);
int   CoreSetParam(uint64_t handle, uint32_t id, void *value);
int   CoreGetPPList(uint64_t handle, void *out, uint32_t cch, uint32_t *count);
int   CoreGetVoiceList(uint64_t handle, void *out, uint32_t cch, uint32_t *count);
int   CoreGetLanguageList(uint64_t handle, void *out, uint32_t cch, uint32_t *count);
int   CoreSetDictionaries(uint64_t handle, void *list, uint32_t count);
int   CoreSetDictionaryCb(uint64_t handle, void *cb);
const wchar_t *Core_ErrorGetString(int code);
const wchar_t *Core_ErrorGetModuleName(int code);
const wchar_t *Core_ErrorGetSubModuleName(int code);

/* ------------------------------------------------------------------ SAPI4 module ids */
#define SAPI4_CLSID_ENGINE      "{A162A680-7689-4B2E-8A9B-6806F141EFC5}"
#define SAPI4_IID_ENUM          "{6B837B20-4A47-101B-931A-00AA0047BA4F}"
#define SAPI4_IID_CENTRAL       "{1287A280-4A47-101B-931A-00AA0047BA4F}"
#define SAPI4_IID_NOTIFY_SINK   "{C0FA8F40-4A46-101B-931A-00AA0047BA4F}"
#define SAPI4_IID_ATTRIBUTES    "{1D59DED1-E367-11D1-BED7-006008317CE8}"
#define SAPI4_GUID_TEXT_FORMAT  "{E4963D40-C743-11CD-80E5-00AA003E4B50}"
#define SAPI4_ENGINE_NAME       "ScanSoft RealSpeak v2 Standard"

#ifdef __cplusplus
}
#endif
#endif /* LHS_TTS_H */
