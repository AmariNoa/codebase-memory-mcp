#ifndef CBM_WIN_UTF8_H
#define CBM_WIN_UTF8_H

#ifdef _WIN32

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <stdlib.h>
#include <wchar.h>

static inline wchar_t *cbm_utf8_to_wide(const char *utf8) {
    if (!utf8) {
        return NULL;
    }
    int len = MultiByteToWideChar(CP_UTF8, 0, utf8, -1, NULL, 0);
    if (len <= 0) {
        return NULL;
    }
    wchar_t *w = (wchar_t *)malloc((size_t)len * sizeof(wchar_t));
    if (w) {
        MultiByteToWideChar(CP_UTF8, 0, utf8, -1, w, len);
    }
    return w;
}

static inline char *cbm_wide_to_utf8(const wchar_t *wide) {
    if (!wide) {
        return NULL;
    }
    int len = WideCharToMultiByte(CP_UTF8, 0, wide, -1, NULL, 0, NULL, NULL);
    if (len <= 0) {
        return NULL;
    }
    char *u8 = (char *)malloc((size_t)len);
    if (u8) {
        WideCharToMultiByte(CP_UTF8, 0, wide, -1, u8, len, NULL, NULL);
    }
    return u8;
}

/* Prefix a clean absolute wide path with the \\?\ extended-length marker so
 * Win32 file APIs bypass the legacy MAX_PATH (260) limit. `full` must be a heap
 * buffer holding a GetFullPathNameW-normalized path (backslashes, no '.'/'..').
 * It is freed here. Returns a new heap string, or `full` unchanged when the
 * shape is not a drive or UNC path. */
static inline wchar_t *cbm_win_prefix_longpath(wchar_t *full) {
    size_t n = wcslen(full);
    if (full[0] == L'\\' && full[1] == L'\\') {
        /* UNC "\\server\share\..." -> "\\?\UNC\server\share\..." */
        static const wchar_t PFX[] = L"\\\\?\\UNC\\"; /* 8 wchars */
        wchar_t *out = (wchar_t *)malloc((n + 8) * sizeof(wchar_t));
        if (!out) {
            return full;
        }
        wmemcpy(out, PFX, 8);
        wmemcpy(out + 8, full + 2, (n - 2) + 1); /* tail + NUL */
        free(full);
        return out;
    }
    if (((full[0] >= L'A' && full[0] <= L'Z') || (full[0] >= L'a' && full[0] <= L'z')) &&
        full[1] == L':' && full[2] == L'\\') {
        /* Drive "C:\..." -> "\\?\C:\..." */
        static const wchar_t PFX[] = L"\\\\?\\"; /* 4 wchars */
        wchar_t *out = (wchar_t *)malloc((n + 5) * sizeof(wchar_t));
        if (!out) {
            return full;
        }
        wmemcpy(out, PFX, 4);
        wmemcpy(out + 4, full, n + 1); /* whole path + NUL */
        free(full);
        return out;
    }
    return full;
}

/* Convert a UTF-8 filesystem path to a wide \\?\-prefixed extended-length path.
 * Use ONLY for path arguments; mode strings and command lines must use
 * cbm_utf8_to_wide. Returns a heap wide string (caller frees) or NULL on
 * failure. Falls back to the plain / GetFullPathNameW-resolved path when a
 * \\?\ prefix does not apply. */
static inline wchar_t *cbm_utf8_to_wide_path(const char *utf8) {
    wchar_t *w = cbm_utf8_to_wide(utf8);
    if (!w) {
        return NULL;
    }
    /* Already an extended-length (\\?\) or device (\\.\) path — use verbatim. */
    if (w[0] == L'\\' && w[1] == L'\\' && (w[2] == L'?' || w[2] == L'.') && w[3] == L'\\') {
        return w;
    }
    /* \\?\ paths require backslash separators; normalize any forward slashes
     * (our paths are built with "%s/%s"). */
    for (wchar_t *p = w; *p; p++) {
        if (*p == L'/') {
            *p = L'\\';
        }
    }
    int is_unc = (w[0] == L'\\' && w[1] == L'\\');
    int is_drive = ((w[0] >= L'A' && w[0] <= L'Z') || (w[0] >= L'a' && w[0] <= L'z')) &&
                   w[1] == L':' && w[2] == L'\\';
    if (is_unc || is_drive) {
        /* Absolute path: prefix directly WITHOUT GetFullPathNameW. Its input is
         * bound by MAX_PATH unless already \\?\-prefixed, so calling it on the
         * long absolute paths this helper exists for would fail — the very case
         * we must handle. Collapse duplicate backslashes first (a "%s/%s" join
         * over a trailing separator yields "\\", invalid inside a \\?\ path),
         * preserving the two leading backslashes of a UNC name. */
        size_t start = is_unc ? 2 : 0;
        wchar_t *dst = w + start;
        for (wchar_t *p = w + start; *p; p++) {
            if (*p == L'\\' && dst > w + start && dst[-1] == L'\\') {
                continue;
            }
            *dst++ = *p;
        }
        *dst = L'\0';
        return cbm_win_prefix_longpath(w);
    }
    /* Relative or unusual shape (input within MAX_PATH): resolve to absolute via
     * GetFullPathNameW, then prefix. */
    DWORD need = GetFullPathNameW(w, 0, NULL, NULL); /* required size incl. NUL */
    if (need == 0) {
        return w; /* best effort */
    }
    wchar_t *full = (wchar_t *)malloc((size_t)need * sizeof(wchar_t));
    if (!full) {
        return w;
    }
    DWORD got = GetFullPathNameW(w, need, full, NULL);
    if (got == 0 || got >= need) {
        free(full);
        return w;
    }
    free(w);
    /* full uses backslashes from GetFullPathNameW; forward-slash normalization
     * above does not apply to it, but GetFullPathNameW never emits '/'. */
    return cbm_win_prefix_longpath(full);
}

#endif /* _WIN32 */
#endif /* CBM_WIN_UTF8_H */
