#include <windows.h>
#include <commctrl.h>
#ifndef __cplusplus
#   include <stdbool.h>
#endif

static inline char **
parseCmdlineToArgvA(const char *argstr, int *pargc)
{
    int argc = 1;
    int numchars = 0;
    const char *str = argstr;

    /* step 1: count arguments and characters */

    // program name is special
    if (*str == '"') {
        ++str;
        while (*str != '"' && *str) {
            ++str;
            ++numchars;
        }

        if (*str == '"')
            ++str; // skip over "
    } else {
        while (*str != ' ' && *str != '\t' && *str) {
            ++str;
            ++numchars;
        }
    }

    while (*str == ' ' || *str == '\t')
        ++str;

    // now the arguments
    while (*str) {
        ++argc;

        int iq = 0;
        while (*str && (iq || (*str != ' ' && *str != '\t'))) {
            if (*str == '"' && !iq) {
                iq = 1;
                ++str;
            } else if (*str == '"' && iq) {
                ++str;
                if (*str == '"') {
                    ++numchars;
                    ++str; // remain in quotes mode
                } else {
                    iq = 0;
                }
            } else if (*str == '\\') {
                int bc = 1;
                ++str;
                while (*str == '\\') {
                    ++bc;
                    ++str;
                }
                if (*str == '"') {
                    if (bc % 2 == 1) { // escaped double quote
                        ++str;
                        numchars += bc / 2 + 1;
                    } else {
                        numchars += bc / 2;
                    }
                } else {
                    numchars += bc;
                }
            } else {
                ++str;
                ++numchars;
            }
        }

        while (*str == ' ' || *str == '\t')
            ++str;
    }

    /* step 2: create argument array */
    char *buf = (char*)LocalAlloc(LMEM_FIXED, (argc + 1) * sizeof(char*) + argc + numchars);
    char **argv = (char **)buf;
    buf += (argc + 1) * sizeof(char*);

    char **pargi = argv;
    str = argstr;

    // program name is special
    *pargi = buf;
    if (*str == '"') {
        ++str;
        while (*str != '"' && *str) {
            *buf++ = *str++;
        }

        if (*str == '"')
            ++str; // skip over "
    } else {
        while (*str != ' ' && *str != '\t' && *str)
            *buf++ = *str++;
    }
    *buf++ = 0;

    while (*str == ' ' || *str == '\t')
        ++str;

    // now the arguments
    while (*str) {
        *++pargi = buf;

        int iq = 0;
        while (*str && (iq || (*str != ' ' && *str != '\t'))) {
            if (*str == '"' && !iq) {
                iq = 1;
                ++str;
            } else if (*str == '"' && iq) {
                ++str;
                if (*str == '"') {
                    *buf++ = '"';
                    ++str; // remain in quotes mode
                } else {
                    iq = 0;
                }
            } else if (*str == '\\') {
                int bc = 1;
                ++str;
                while (*str == '\\') {
                    ++bc;
                    ++str;
                }
                if (*str == '"') {
                    if (bc % 2 == 1) { // escaped double quote
                        ++str;
                        for (int i = 0; i < bc/2; ++i)
                            *buf++ = '\\';
                        *buf++ = '"';
                    } else {
                        for (int i = 0; i < bc/2; ++i)
                            *buf++ = '\\';
                    }
                } else {
                    for (int i = 0; i < bc; ++i)
                        *buf++ = '\\';
                }
            } else {
                *buf++ = *str++;
            }
        }

        *buf++ = 0;

        while (*str == ' ' || *str == '\t')
            ++str;
    }

    *++pargi = NULL;

    if (pargc)
        *pargc = argc;

    return argv;
}

static inline bool
isRunningAsAdmin()
{
    BOOL retval = FALSE;
    PSID adminGroupSid = NULL;
    SID_IDENTIFIER_AUTHORITY sidAuth = SECURITY_NT_AUTHORITY;

    BOOL (WINAPI *pCheckTokenMembership)(HANDLE, PSID, PBOOL) = NULL;

    HMODULE advapi = GetModuleHandleA("advapi32.dll");

    *((void**)&pCheckTokenMembership) = (void*)GetProcAddress(advapi, "CheckTokenMembership");

    if (!pCheckTokenMembership) {
        // Win9x and NT4 are missing this function, just assume we're having admin rights
        retval = TRUE;
        goto out;
    }

    if (!AllocateAndInitializeSid(&sidAuth, 2, SECURITY_BUILTIN_DOMAIN_RID, DOMAIN_ALIAS_RID_ADMINS,
            0, 0, 0, 0, 0, 0, &adminGroupSid)) {
        goto out;
    }

    if (!pCheckTokenMembership(NULL, adminGroupSid, &retval)) {
        goto out;
    }

out:
    if (adminGroupSid)
        FreeSid(adminGroupSid);

    return !!retval;
}

static inline bool
isPendingRestart()
{
    bool retval = false;
    HKEY smKey = NULL;
    LONG s = RegOpenKeyExA(HKEY_LOCAL_MACHINE,
                           "SYSTEM\\CurrentControlSet\\Control\\Session Manager",
                           0,
                           KEY_READ,
                           &smKey);
    if (s == ERROR_SUCCESS) {
        s = RegQueryValueExA(smKey, "PendingFileRenameOperations", NULL, NULL, NULL, NULL);

        if (s == ERROR_SUCCESS) {
            retval = true;
        }

        RegCloseKey(smKey);
    }

    return retval;
}

static inline void
wcsOverrideTail(WCHAR *buf, const WCHAR *tail)
{
    int buflen = lstrlenW(buf);
    int taillen = lstrlenW(tail);

    if (taillen > buflen) // WTF!???
        return;

    for (int i = 0; i < taillen; ++i) {
        buf[buflen - taillen + i] = tail[i];
    }
}

static inline void
strOverrideTail(char *buf, const char *tail)
{
    int buflen = lstrlenA(buf);
    int taillen = lstrlenA(tail);

    if (taillen > buflen) // WTF!???
        return;

    for (int i = 0; i < taillen; ++i) {
        buf[buflen - taillen + i] = tail[i];
    }
}

static inline bool
fileExistsW(const WCHAR *p)
{
  DWORD a = GetFileAttributesW(p);

  return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static inline bool
fileExistsA(const char *p)
{
  DWORD a = GetFileAttributesA(p);

  return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static inline void
compatAllowSetForegroundWindow(DWORD pid)
{
    BOOL (WINAPI *pAllowSetForegroundWindow)(DWORD);
    HMODULE user32 = GetModuleHandleA("USER32.DLL");
    *(void**)&pAllowSetForegroundWindow = (void*)GetProcAddress(user32, "AllowSetForegroundWindow");
    if (pAllowSetForegroundWindow)
        pAllowSetForegroundWindow(pid);
}

static inline int
launchRundllA(const char *infpath, int flags, const char *rebootmode)
{
    char sysdir[MAX_PATH];
    GetWindowsDirectoryA(sysdir, MAX_PATH);

    char rundllbuf[1024];
    char advinfbuf[100] = "";
    GetPrivateProfileStringA("Version", "AdvancedINF", "", advinfbuf, sizeof(advinfbuf)/sizeof(advinfbuf[0]), infpath);
    if (lstrlenA(advinfbuf) > 0) {
        wsprintfA(rundllbuf, "%s\\rundll32.exe advpack.dll,LaunchINFSectionEx \"%s\",DefaultInstall,,%d,%s", sysdir, infpath, flags, rebootmode);
    } else {
        flags = 128 + (lstrcmpA(rebootmode, "N") ? 4 : 0);
        wsprintfA(rundllbuf, "%s\\rundll32.exe setupapi.dll,InstallHinfSection DefaultInstall %d %s", sysdir, flags, infpath);
    }

    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof(pi));

    STARTUPINFOA si;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);

    BOOL r = CreateProcessA(NULL,
                            rundllbuf,
                            NULL,
                            NULL,
                            FALSE,
                            CREATE_SUSPENDED,
                            NULL,
                            NULL,
                            &si,
                            &pi);
    if (!r) {
        MessageBoxA(NULL, "Failed to launch rundll32.exe", "Error", MB_ICONHAND|MB_OK);
        return 1;
    }

    // HACK! On WinXP and possibly earlier, need this or dialogs made by rundll32
    // are hidden when being run from iexpress.
    compatAllowSetForegroundWindow(pi.dwProcessId);

    ResumeThread(pi.hThread);

    for (;;) {
        DWORD m = MsgWaitForMultipleObjects(1, &pi.hProcess, FALSE, INFINITE, QS_ALLEVENTS);
        if (m == WAIT_OBJECT_0) {
            break;
        } else {
            MSG msg;
            while (PeekMessageA(&msg, NULL, 0, 0, PM_REMOVE)) {
                TranslateMessage(&msg);
                DispatchMessageA(&msg);
            }
        }
    }

    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);

    return 0;
}

static inline HRESULT
launchRundllW(const WCHAR *infpath, int flags, const char *rebootmode)
{
    // launch advpack.dll via rundll32
    // don't load it directly, because the uninstaller is using rundll32 too
    // and we don't want any compat shims being applied to the installer but not the uninstaller

    // Load 64bit rundll32 if available. On Windows 10, the Settings app will launch
    // the uninstall rundll32.exe as 64bit, even for 32bit apps and then files and
    // registry keys are not removed correctly. Windows 7 to 8.1 correctly launch
    // a 32bit rundll32.exe for 32bit uninstall information, BTW.
    WCHAR rundllpath[MAX_PATH];
    GetWindowsDirectoryW(rundllpath, MAX_PATH);
    lstrcatW(rundllpath, L"\\SysNative\\rundll32.exe");

    if (!fileExistsW(rundllpath)) {
        GetSystemDirectoryW(rundllpath, MAX_PATH);
        lstrcatW(rundllpath, L"\\rundll32.exe");
    }

    WCHAR rundllbuf[1024];
    WCHAR advinfbuf[100] = L"";
    GetPrivateProfileStringW(L"Version", L"AdvancedINF", L"", advinfbuf, sizeof(advinfbuf)/sizeof(advinfbuf[0]), infpath);

    if (lstrlenW(advinfbuf) > 0) {
        wsprintfW(rundllbuf, L"%s advpack.dll,LaunchINFSectionEx \"%s\",DefaultInstall,,%d,%S", rundllpath, infpath, flags, rebootmode);
    } else {
        flags = 128 + (lstrcmpA(rebootmode, "N") ? 4 : 0);
        wsprintfW(rundllbuf, L"%s setupapi.dll,InstallHinfSection DefaultInstall %d %s", rundllpath, flags, infpath);
    }

    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof(pi));

    STARTUPINFOW si;
    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);

    BOOL r = CreateProcessW(NULL,
                            rundllbuf,
                            NULL,
                            NULL,
                            FALSE,
                            CREATE_SUSPENDED,
                            NULL,
                            NULL,
                            &si,
                            &pi);
    if (!r) {
        MessageBoxW(NULL, L"Failed to launch rundll32.exe", L"Error", MB_ICONHAND|MB_OK);
        return E_FAIL;
    }

    // HACK! On WinXP and possibly earlier, need this or dialogs made by rundll32
    // are hidden when being run from iexpress.
    compatAllowSetForegroundWindow(pi.dwProcessId);

    ResumeThread(pi.hThread);

    for (;;) {
        DWORD m = MsgWaitForMultipleObjects(1, &pi.hProcess, FALSE, INFINITE, QS_ALLEVENTS);
        if (m == WAIT_OBJECT_0) {
            break;
        } else {
            MSG msg;
            while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
        }
    }

    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);

    return S_OK;
}

static inline HRESULT
restartAsAdmin(void)
{
    const WCHAR *cmdline = GetCommandLineW();

    // skip over program name to parameters
    if (*cmdline == '"') {
        ++cmdline;
        while (*cmdline != '"' && *cmdline) {
            ++cmdline;
        }

        if (*cmdline == '"')
            ++cmdline; // skip over "
    } else {
        while (*cmdline != ' ' && *cmdline != '\t' && *cmdline) {
            ++cmdline;
        }
    }

    while (*cmdline == ' ' || *cmdline == '\t')
        ++cmdline;

    // build new parameters list
    WCHAR params[1024] = L"";
    wsprintfW(params, L"/uacrestart %s", cmdline);

    // find our own path
    WCHAR exefile[MAX_PATH] = {0};
    GetModuleFileNameW(NULL, exefile, MAX_PATH);
    exefile[MAX_PATH-1] = 0;

    // now run us
    SHELLEXECUTEINFOW sei;
    ZeroMemory(&sei, sizeof(sei));
    sei.cbSize = sizeof(sei);
    sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_UNICODE | SEE_MASK_FLAG_DDEWAIT;
    sei.lpVerb = L"runas";
    sei.lpFile = exefile;
    sei.lpParameters = params;
    sei.nShow = SW_SHOWDEFAULT;

    BOOL (WINAPI *pShellExecuteExW)(SHELLEXECUTEINFOW*);
    HMODULE hShell32 = LoadLibraryW(L"SHELL32.DLL");
    *(void**)&pShellExecuteExW = (void*)GetProcAddress(hShell32, "ShellExecuteExW");
    pShellExecuteExW(&sei);
    FreeLibrary(hShell32);

    if ((int)sei.hInstApp < 32) {
        return E_FAIL;
    }

    if (sei.hProcess) {
        for (;;) {
            DWORD m = MsgWaitForMultipleObjects(1, &sei.hProcess, FALSE, INFINITE, QS_ALLEVENTS);
            if (m == WAIT_OBJECT_0) {
                break;
            } else {
                MSG msg;
                while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
                    TranslateMessage(&msg);
                    DispatchMessageW(&msg);
                }
            }
        }

        DWORD exitcode = (DWORD)S_OK;
        GetExitCodeProcess(sei.hProcess, &exitcode);
        CloseHandle(sei.hProcess);

        return (HRESULT)exitcode;
    }

    return S_OK;
}

static inline HRESULT
run(void)
{
    InitCommonControls();

    // parse command line args
    int flags = 0;
    char rebootmode[2] = "";
    int uacrestart = 0;

    char **argv = parseCmdlineToArgvA(GetCommandLineA(), NULL);
    for (int i = 1; argv[i]; ++i) {
        if (!lstrcmpiA(argv[i], "/quiet")) {
            flags = 4;
        } else if (!lstrcmpiA(argv[i], "/norestart")) {
            lstrcpyA(rebootmode, "N");
        } else if (!lstrcmpiA(argv[i], "/uacrestart")){
            uacrestart = 1;
        } else {
            MessageBoxW(NULL, L"Supported Arguments:\r\n\r\n\t/quiet - do not show UI\r\n\t/norestart - never ask to reboot", L"Error", MB_ICONHAND|MB_OK);
            return E_FAIL;
        }
    }
    LocalFree(argv);

    // check for admin rights
    if (!isRunningAsAdmin()) {
        if (LOBYTE(LOWORD(GetVersion())) >= 6 && !uacrestart) {
            return restartAsAdmin();
        } else {
            MessageBoxW(NULL, L"You need to run this program as administrator", L"Error", MB_ICONHAND|MB_OK);
            return E_FAIL;
        }
    }

    // check for pending install restart
    if (isPendingRestart()) {
        MessageBoxW(NULL, L"A previous installation requires a restart to complete. Please restart your computer and then try again.", L"Error", MB_ICONHAND|MB_OK);
        return E_FAIL;
    }

    if (GetVersion() < 0x80000000) {
        // NT -> use unicode

        // find own path name
        WCHAR path[MAX_PATH] = {0};
        GetModuleFileNameW(NULL, &path[0], MAX_PATH);
        path[MAX_PATH-1] = 0; // pre-Vista is not guaranteed to 0-terminate the string


        // find INF file
        wcsOverrideTail(path, L".INF");
        if (!fileExistsW(path)) {
            WCHAR buf[1024];
            wsprintfW(buf, L"Couldn't find INF file: %s", path);
            MessageBoxW(NULL, buf, L"Error", MB_ICONHAND|MB_OK);
            return E_FAIL;
        }

        return launchRundllW(path, flags, rebootmode);
    } else {
        // Win9x -> use ANSI codepage

        // find own path
        char path[MAX_PATH] = {0};
        GetModuleFileNameA(NULL, &path[0], MAX_PATH);
        path[MAX_PATH-1] = 0; // pre-Vista is not guaranteed to 0-terminate the string

        // find INF file
        strOverrideTail(path, ".INF");
        if (!fileExistsA(path)) {
            char buf[1024];
            wsprintfA(buf, "Couldn't find INF file: %s", path);
            MessageBoxA(NULL, buf, "Error", MB_ICONHAND|MB_OK);
            return E_FAIL;
        }

        return launchRundllA(path, flags, rebootmode);
    }
}

// entry point for MinGW builds
int WINAPI
WinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance,  PSTR lpCmdLine, INT nCmdShow)
{
    (void)hInstance;
    (void)hPrevInstance;
    (void)lpCmdLine;
    (void)nCmdShow;

    return run();
}

// entry point for MSVC builds without CRT
EXTERN_C int WINAPI
msvcWinMainCRTStartup()
{
    ExitProcess((UINT)run());
    return 0;
}

