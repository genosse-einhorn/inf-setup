#include <windows.h>

EXTERN_C int WINAPI
msvcWinMainCRTStartup()
{
    MessageBoxA(NULL, "Hello, World!", "Hello", MB_OK|MB_ICONASTERISK);

    ExitProcess(0);
}

