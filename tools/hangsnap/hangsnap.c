/* hangsnap: snapshot a running (usually hung) mercenaries_recomp.exe.
 *
 *   hangsnap <pid> <globals_rva_hex> <globals_size_hex> <esp_offset_hex>
 *
 * Prints, in a line format `python -m mercfoundry hangsnap` parses:
 *
 *   base <image base> <image size>
 *   globals <rva> <bytes read>
 *     <32-bit words, 8 per line>
 *   guest-stack <guest esp>
 *     <32-bit words, 8 per line>
 *   thread <id> [<description>]
 *     <frames, one per line: exe+<rva> or <module>+<offset>>
 *
 * globals_rva is the RVA of g_xbox_mem_offset (the start of the port's
 * register/trace block); esp_offset is g_esp's offset from it. Both come
 * from the port's linker map; this program knows nothing about the port.
 *
 * Read-only apart from suspending each thread while its stack is walked.
 * Build with build.cmd. */
#include <windows.h>
#include <tlhelp32.h>
#include <psapi.h>
#include <dbghelp.h>
#include <stdio.h>
#include <stdlib.h>
#pragma comment(lib, "dbghelp.lib")
#pragma comment(lib, "psapi.lib")

#define MAX_FRAMES 64
#define GUEST_STACK_WORDS 128

static void dump_words(const unsigned char *p, SIZE_T n)
{
    for (SIZE_T i = 0; i + 4 <= n; i += 4)
        printf("%s%08lX", (i % 32) ? " " : "\n  ", *(const DWORD *)(p + i));
    printf("\n");
}

int main(int argc, char **argv)
{
    if (argc < 5) {
        fprintf(stderr, "usage: hangsnap <pid> <globals_rva_hex> <globals_size_hex> <esp_offset_hex>\n");
        return 2;
    }
    DWORD pid = strtoul(argv[1], NULL, 10);
    DWORD64 grva = _strtoui64(argv[2], NULL, 16);
    DWORD gsize = strtoul(argv[3], NULL, 16);
    DWORD espoff = strtoul(argv[4], NULL, 16);
    if (gsize < 8 || espoff + 4 > gsize) { fprintf(stderr, "bad globals size or esp offset\n"); return 2; }

    HANDLE proc = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, FALSE, pid);
    if (!proc) { fprintf(stderr, "OpenProcess(%lu) failed: error %lu\n", pid, GetLastError()); return 1; }
    HMODULE mods[1]; DWORD needed;
    if (!EnumProcessModules(proc, mods, sizeof(mods), &needed)) {
        fprintf(stderr, "EnumProcessModules failed: error %lu\n", GetLastError());
        return 1;
    }
    DWORD64 base = (DWORD64)mods[0];
    MODULEINFO mi = {0};
    GetModuleInformation(proc, mods[0], &mi, sizeof(mi));
    printf("base %llX %lX\n", base, mi.SizeOfImage);

    unsigned char *g = malloc(gsize);
    SIZE_T got = 0;
    if (g && ReadProcessMemory(proc, (LPCVOID)(base + grva), g, gsize, &got)) {
        printf("globals %llX %llX", grva, (unsigned long long)got);
        dump_words(g, got);
        DWORD64 memoff = *(DWORD64 *)g;          /* g_xbox_mem_offset comes first */
        DWORD esp = *(DWORD *)(g + espoff);
        DWORD stack[GUEST_STACK_WORDS];
        SIZE_T sgot = 0;
        if (memoff && esp && ReadProcessMemory(proc, (LPCVOID)(memoff + esp), stack, sizeof(stack), &sgot)) {
            printf("guest-stack %08lX", esp);
            dump_words((unsigned char *)stack, sgot);
        }
    } else {
        fprintf(stderr, "could not read the globals block: error %lu\n", GetLastError());
    }
    free(g);

    SymSetOptions(SYMOPT_DEFERRED_LOADS);
    SymInitialize(proc, NULL, TRUE);
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    THREADENTRY32 te;
    te.dwSize = sizeof(te);
    for (BOOL ok = Thread32First(snap, &te); ok; ok = Thread32Next(snap, &te)) {
        if (te.th32OwnerProcessID != pid) continue;
        HANDLE th = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION |
                               THREAD_QUERY_LIMITED_INFORMATION, FALSE, te.th32ThreadID);
        if (!th) continue;
        printf("thread %lu", te.th32ThreadID);
        PWSTR desc = NULL;
        if (SUCCEEDED(GetThreadDescription(th, &desc)) && desc) {
            if (desc[0]) printf(" %ls", desc);
            LocalFree(desc);
        }
        printf("\n");
        if (SuspendThread(th) == (DWORD)-1) { CloseHandle(th); continue; }
        CONTEXT ctx;
        memset(&ctx, 0, sizeof(ctx));
        ctx.ContextFlags = CONTEXT_FULL;
        if (GetThreadContext(th, &ctx)) {
            STACKFRAME64 sf;
            memset(&sf, 0, sizeof(sf));
            sf.AddrPC.Offset = ctx.Rip;    sf.AddrPC.Mode = AddrModeFlat;
            sf.AddrFrame.Offset = ctx.Rbp; sf.AddrFrame.Mode = AddrModeFlat;
            sf.AddrStack.Offset = ctx.Rsp; sf.AddrStack.Mode = AddrModeFlat;
            for (int n = 0; n < MAX_FRAMES; n++) {
                if (!StackWalk64(IMAGE_FILE_MACHINE_AMD64, proc, th, &sf, &ctx, NULL,
                                 SymFunctionTableAccess64, SymGetModuleBase64, NULL)) break;
                DWORD64 pc = sf.AddrPC.Offset;
                if (!pc) break;
                if (pc >= base && pc < base + mi.SizeOfImage) {
                    printf("  exe+%llX\n", pc - base);
                } else {
                    char name[MAX_PATH] = "?";
                    DWORD64 mb = SymGetModuleBase64(proc, pc);
                    if (mb) GetModuleBaseNameA(proc, (HMODULE)mb, name, sizeof(name));
                    printf("  %s+%llX\n", name, mb ? pc - mb : pc);
                }
            }
        }
        ResumeThread(th);
        CloseHandle(th);
    }
    CloseHandle(snap);
    SymCleanup(proc);
    CloseHandle(proc);
    return 0;
}
