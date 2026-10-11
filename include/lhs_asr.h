/* lhs_asr.h — ABI/constants for the Dragon speech-recognition subsystem (MREC based).
 *
 * Reconstructed from mrec.dll (module map + internal API names), the shipped DFILE assets,
 * the installer registry, and dnsapi.chm (SDK help).  See SPEC_ASR.md.
 *
 * The recognition engine ships as mrec.dll (private, no useful exports) driven by the
 * per-language dd10r<lang>.dll / au10r<lang>.dll pair; the product's public surfaces are the
 * COM engine objects (CLSIDs below) and the automation objects exposed by dnstk10.dll
 * (typelib {5C486340-2F92-11D1-A47C-00A024A3A678}).
 */
#ifndef LHS_ASR_H
#define LHS_ASR_H

#include <stdint.h>
#include <wchar.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------ DFILE v10 container */
#define DFILE_SIGNATURE      "DFILE v10"
#define DFILE_SIGNATURE_LEN  16
#define DFILE_MARKER_HEADER  0xEDACFF0Eu      /* alternate/encrypted: 0xD2CEFADE */
#define DFILE_MARKER_TRAILER 0xEDACFF2Eu
#define DFILE_FLAGS          0xFFFE0002u      /* u16 0xFFFE checked; 0xFEFF = obsolete format */
#define DFILE_PAGE_4096      0x1000u
#define DFILE_PAGE_256       0x100u

/* subfile coding variants (low byte of the codec selector word) */
#define DFILE_CODEC_2        2
#define DFILE_CODEC_3        3
#define DFILE_CODEC_4        4
#define DFILE_CODEC_5        5
#define DFILE_CODEC_6        6
#define DFILE_CODEC_9        9
#define DFILE_CODEC_12       0x12

/* subfile classes referenced by the engine */
#define DFILE_CLASS_VOC      "Voc"
#define DFILE_CLASS_SIGPROC  "SigProc"
#define DFILE_CLASS_USER     "User"
#define DFILE_CLASS_PARAMSET "ParamSetHolder"
#define DFILE_CLASS_ENV      "EnvHolder"

typedef struct dfile_header {
    char     signature[16];   /* "DFILE v10" NUL padded        */
    uint32_t version;         /* 10                            */
    uint16_t format_marker;   /* 0xFFFE                        */
    uint16_t reserved;        /* 0x0002                        */
    uint32_t page_size;       /* 0x1000 or 0x100               */
    uint32_t entry_count;     /* subfile/block entries         */
    uint32_t marker;          /* 0xEDACFF0E                    */
    uint32_t data_size;       /* size/checksum word            */
    uint32_t codec_selector;  /* low byte = coding variant     */
} dfile_header_t;

/* Subfile directory entry (class `RealDFileSubFileEntry`); roles of the three numeric fields are
 * not yet separated (offset / length / checksum family). */
typedef struct dfile_subfile_entry {
    const char *name;
    const char *class_name;
    uint32_t    field1;
    uint32_t    field2;
    uint32_t    field3;
} dfile_subfile_entry_t;

/* ------------------------------------------------------------------ internal engine API
 * Names verified in mrec.dll's string table.  Parameter lists are reconstructed
 * (see SPEC_ASR.md §4) — treat them as the intended shape, not a byte-exact ABI.
 */
int      RDApi_IsDFile(const wchar_t *path);
int      RDApi_GetDFileInfo(const wchar_t *path, void *out_info);
int      RDApi_GetDFileData(const wchar_t *path, uint64_t offset, void *out, uint32_t bytes);
const char *SDApi_GetFileRecognizerVersion(void);      /* "1.2.5.f-MREC-alien" */
int      SDDiag_TestReadFile(const wchar_t *path, void *out);

int      SDRecog_RecognizerGetNumRecogLayers(void *recognizer, uint32_t *out_layers);
int      SDRecog_RecognizeNonAcoustic(void *recognizer, const void *in, void **out);
int      SDRecog_GuessPronsAcoustic(void *recognizer, const wchar_t *word, void **out_list);
int      SDRecog_GuessPronsNonAcoustic(void *recognizer, const wchar_t *word, void **out_list);
int      SDRecogResult_NewListFromRecognizer(void *recognizer, void **out_list);
int      SDLattice_NewFromRecogResult(void *result, void **out_lattice);
int      SDSausage_NewFromSausageSplitNonAcoustic(void *in, void **out_sausage);
int      SDhAcousticScoringParamSet(void *recognizer, void *param_set);
int      SDState_GetPrecedingNonAcousticWordState(void *state, void **out);

/* ------------------------------------------------------------------ product COM surface */
#define SR_ENGINE_CLSID        "{dd100001-6205-11cf-ae61-0000e8a28647}"  /* Voicebar SR enumerator */
#define SR_SPEECH_SERVICES_CLSID "{dd100002-6205-11cf-ae61-0000e8a28647}"
#define SR_VOICEDICTATE_CLSID  "{dd100003-6205-11cf-ae61-0000e8a28647}"
#define SR_VOICECOMMANDS_CLSID "{607fc1b2-70ae-11d1-a49f-00a024a3a678}"
#define SR_ENGINE_REG_PATH     "SOFTWARE\\Voice\\SpeechRecognition\\Engine"
#define SR_ENGINE_REG_NAME     "Dragon NaturallySpeaking"

/* Automation typelib / objects (dnstk10.dll) */
#define DGN_AUTOMATION_TYPELIB "{5C486340-2F92-11D1-A47C-00A024A3A678}"
/* objects: DgnEngineControl, DgnSpeechEngines, DgnSpeechEngine, DgnResult, DgnStrings,
 * DgnLongLong; event sources: DgnEngineControlEvents, DgnMicBtnEvents, DgnVCmdEvents.
 * Member/enum inventory: SPEC_ASR.md §5, sdkdocs/ (extracted from dnsapi.chm). */

/* ------------------------------------------------------------------ per-language modules */
/* Recogniser / acoustic module naming: dd10r<lang>.dll + au10r<lang>.dll, each with an _x64 twin.
 * Language codes match the TTS side: enx enu eng enc aus ind sea deu esp fra ita nld jpn. */

#ifdef __cplusplus
}
#endif
#endif /* LHS_ASR_H */
