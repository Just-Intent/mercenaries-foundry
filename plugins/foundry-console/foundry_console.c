/* Foundry Console: a developer window for Mercenaries Recompiled (F7).
 *
 *   Inspector  - gameplay state, the player's position, heading, money and
 *                hit points, and the game's recent spawns.
 *   Spawn      - any object template by name (searchable list from
 *                templates.tsv, written by `python -m mercfoundry templates`),
 *                placed 8 m ahead of the player; "Loaded here only" asks the
 *                game which templates the current area has loaded.
 *   Lua        - run a line of Lua in a loaded script's state (an
 *                expression shows its values; a statement shows "ok").
 *
 * The window runs on its own thread. Everything that touches the game is
 * queued and done in on_update, on the game's thread, through the plugin
 * API (call_guest, hooks) and the Foundry function map.
 */
#define MERC_PLUGIN_IMPLEMENTATION
#include <windows.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#include "mercenaries_plugin.h"
#include "mercfoundry_functions.h"
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")

#define PLAYER0_HASH 0x660E4490u      /* HashName("player0") */
#define NAME_PROPERTY_HASH 0x8D39BDE6u /* HashName("name") */
#define MAX_TEMPLATES 4096
#define SPAWN_DISTANCE 8.0f

typedef struct Template {
    char name[64], layer[40], type[24], faction[16];
    uint32_t hash;
    uint8_t loaded; /* 0 unknown, 1 loaded here, 2 not loaded */
} Template;

static const MercPluginHost *host;
static CRITICAL_SECTION lock;
static Template templates[MAX_TEMPLATES];
static unsigned template_count;

/* Shared between the window and the game thread (under lock). */
static char info_text[1024] = "Waiting for the game...";
static char status_text[256] = "";
static int pending_spawn = -1;
static float pending_money;
static unsigned scan_next = MAX_TEMPLATES; /* < template_count while scanning */
static char recent[5][64];
static unsigned recent_count;
static volatile LONG list_dirty;

/* Lua console: scripts seen loading (a hook after LuaScript_RegisterCommands). */
#define MAX_SCRIPTS 32
typedef struct Script { uint32_t object, L; unsigned serial; } Script;
static Script scripts[MAX_SCRIPTS];
static unsigned script_count, script_serial;
static char pending_lua[1024];
static int pending_lua_serial = -1; /* script serial to run in; 0 = newest; -1 = nothing */
static char lua_output[8192];
static volatile LONG output_dirty, scripts_dirty;

/* Window */
static DWORD window_thread_id;
static HWND wnd, info_label, search_edit, list_box, loaded_check, spawn_button, money_button, status_label;
static HWND script_combo, lua_edit, run_button, output_edit;
static unsigned combo_serials[MAX_SCRIPTS + 1];
static int list_map[MAX_TEMPLATES];
static unsigned list_size;
#define WM_APP_TOGGLE (WM_APP + 1)
#define ID_SEARCH 101
#define ID_LIST 102
#define ID_LOADED 103
#define ID_SPAWN 104
#define ID_MONEY 105
#define ID_TIMER 106
#define ID_SCRIPT 107
#define ID_LUA 108
#define ID_RUN 109

static uint32_t hash_name(const char *s)
{
    uint32_t h = 0x811C9DC5u;
    if (!s || !*s) return 0;
    for (; *s; s++) h = (h ^ (uint32_t)(uint8_t)(*s | 0x20)) * 0x01000193u;
    return h;
}

static void set_status(const char *fmt, ...)
{
    va_list ap;
    EnterCriticalSection(&lock);
    va_start(ap, fmt);
    vsnprintf(status_text, sizeof(status_text), fmt, ap);
    va_end(ap);
    LeaveCriticalSection(&lock);
}

/* ---------- templates.tsv ---------- */

static void field(char *dst, size_t size, const char *src, size_t n)
{
    if (n >= size) n = size - 1;
    memcpy(dst, src, n);
    dst[n] = 0;
}

static void load_templates(void)
{
    char path[MAX_PATH * 3];
    wchar_t wpath[MAX_PATH * 2];
    snprintf(path, sizeof(path), "%s\\templates.tsv", host->plugin_directory);
    if (!MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, MAX_PATH * 2)) return;
    FILE *f = _wfopen(wpath, L"rb");
    if (!f) {
        set_status("No templates.tsv: run python -m mercfoundry templates and reinstall.");
        return;
    }
    char line[512];
    int header = 1;
    while (fgets(line, sizeof(line), f) && template_count < MAX_TEMPLATES) {
        if (header) { header = 0; continue; }
        const char *cols[6] = {0};
        size_t lens[6] = {0};
        char *p = line;
        for (int c = 0; c < 6; c++) {
            cols[c] = p;
            char *end = strpbrk(p, "\t\r\n");
            lens[c] = end ? (size_t)(end - p) : strlen(p);
            if (!end || *end != '\t') { p = end ? end : p + lens[c]; break; }
            p = end + 1;
        }
        if (!cols[0] || !lens[0]) continue;
        Template *t = &templates[template_count++];
        field(t->name, sizeof(t->name), cols[0], lens[0]);
        if (cols[1]) field(t->layer, sizeof(t->layer), cols[1], lens[1]);
        if (cols[2]) field(t->type, sizeof(t->type), cols[2], lens[2]);
        if (cols[3]) field(t->faction, sizeof(t->faction), cols[3], lens[3]);
        t->hash = hash_name(t->name);
    }
    fclose(f);
}

static const char *template_name(uint32_t hash)
{
    for (unsigned i = 0; i < template_count; i++)
        if (templates[i].hash == hash) return templates[i].name;
    return NULL;
}

/* ---------- game thread ---------- */

static int guest(uint32_t fn, uint32_t self, unsigned n, const uint32_t *args, MercGuestCall *c)
{
    memset(c, 0, sizeof(*c));
    c->struct_size = sizeof(*c);
    c->address = fn;
    c->this_ptr = self;
    c->arg_count = n;
    if (n) memcpy(c->args, args, n * 4);
    return host->call_guest(host->context, c);
}

static uint32_t find_player(void)
{
    MercGuestCall c;
    uint32_t hash = PLAYER0_HASH;
    return guest(MF_FN_World_FindActorByName, 0, 1, &hash, &c) ? c.eax : 0;
}

static int template_loaded(uint32_t hash)
{
    MercGuestCall c;
    uint32_t args[2] = {hash, NAME_PROPERTY_HASH};
    return guest(MF_FN_Template_GetProperty, 0, 2, args, &c) && c.eax != 0;
}

static void do_spawn(int index)
{
    const Template *t = &templates[index];
    uint32_t player = find_player(), scratch, size;
    float m[16];
    MercGuestCall c;
    if (!player) { set_status("No player found."); return; }
    if (!template_loaded(t->hash)) {
        set_status("%s isn't loaded in this area.", t->name);
        return;
    }
    if (!host->guest_scratch(host->context, &scratch, &size) || size < 256) {
        set_status("No scratch memory.");
        return;
    }
    host->read_guest(host->context, player + MF_OFF_Actor_transform, m, sizeof(m));
    m[12] += m[8] * SPAWN_DISTANCE;
    m[13] += 1.5f;
    m[14] += m[10] * SPAWN_DISTANCE;
    m[3] = m[7] = m[11] = 0.0f;
    m[15] = 1.0f;
    host->write_guest(host->context, scratch, m, sizeof(m));
    char name[64];
    snprintf(name, sizeof(name), "foundry_%s", t->name);
    host->write_guest(host->context, scratch + 64, name, strlen(name) + 1);
    uint32_t args[5] = {t->hash, scratch + 64, scratch, 0, 0};
    if (guest(MF_FN_Spawner_SpawnTemplate, MF_VAR_g_Spawner, 5, args, &c) && c.eax)
        set_status("Spawned %s.", t->name);
    else
        set_status("Spawning %s failed.", t->name);
}

static void do_money(float amount)
{
    uint32_t player = find_player(), bits;
    float money = 0;
    MercGuestCall c;
    if (!player) return;
    host->read_guest(host->context, player + MF_OFF_Actor_money, &money, 4);
    money += amount;
    memcpy(&bits, &money, 4);
    guest(MF_FN_Player_SetMoney, player, 1, &bits, &c);
    set_status("Added $%.0f.", amount);
}

static void inspect(void)
{
    char text[1024];
    int n = 0;
    int playing = host->in_gameplay(host->context);
    n += snprintf(text + n, sizeof(text) - n, "Gameplay: %s\r\n", playing ? "running" : "not running");
    uint32_t player = playing ? find_player() : 0;
    if (player) {
        float m[16], money = 0, hp = 0;
        host->read_guest(host->context, player + MF_OFF_Actor_transform, m, sizeof(m));
        host->read_guest(host->context, player + MF_OFF_Actor_money, &money, 4);
        host->read_guest(host->context, player + MF_OFF_Actor_hitPoints, &hp, 4);
        double heading = atan2(m[8], m[10]) * 180.0 / 3.14159265358979;
        if (heading < 0) heading += 360.0;
        n += snprintf(text + n, sizeof(text) - n,
                      "Player: 0x%08X\r\nPosition: %.1f, %.1f, %.1f\r\nHeading: %.0f degrees\r\n"
                      "Money: $%.0f\r\nHit points: %.0f\r\n",
                      player, m[12], m[13], m[14], heading, money, hp);
    }
    EnterCriticalSection(&lock);
    n += snprintf(text + n, sizeof(text) - n, "Recent spawns:");
    for (unsigned i = 0; i < recent_count && i < 5; i++)
        n += snprintf(text + n, sizeof(text) - n, "\r\n  %s", recent[(recent_count - 1 - i) % 5]);
    if (!recent_count) n += snprintf(text + n, sizeof(text) - n, " none yet");
    memcpy(info_text, text, sizeof(info_text));
    LeaveCriticalSection(&lock);
}

static void MERC_PLUGIN_CALL spawn_after(void *ud, MercHookContext *c)
{
    (void)ud;
    uint32_t hash = 0;
    char buf[64];
    host->read_guest(host->context, c->esp + 4, &hash, 4);
    const char *name = template_name(hash);
    if (name) snprintf(buf, sizeof(buf), "%s", name);
    else snprintf(buf, sizeof(buf), "template 0x%08X", hash);
    EnterCriticalSection(&lock);
    snprintf(recent[recent_count % 5], sizeof(recent[0]), "%s%s", buf, c->eax ? "" : " (failed)");
    recent_count++;
    LeaveCriticalSection(&lock);
}

static void append_output(const char *fmt, ...)
{
    char line[1200];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    EnterCriticalSection(&lock);
    size_t have = strlen(lua_output), add = strlen(line) + 2;
    if (have + add >= sizeof(lua_output)) {
        /* drop the oldest half */
        const char *cut = strchr(lua_output + sizeof(lua_output) / 2, '\n');
        size_t keep = cut ? strlen(cut + 1) : 0;
        memmove(lua_output, cut ? cut + 1 : lua_output + have, keep + 1);
        have = keep;
    }
    snprintf(lua_output + have, sizeof(lua_output) - have, "%s\r\n", line);
    LeaveCriticalSection(&lock);
    InterlockedExchange(&output_dirty, 1);
}

static void read_text(uint32_t address, char *out, size_t size)
{
    size_t n = 0;
    out[0] = 0;
    while (address && n + 1 < size) {
        char chunk[64];
        size_t want = size - 1 - n < sizeof(chunk) ? size - 1 - n : sizeof(chunk);
        if (!host->read_guest(host->context, address + (uint32_t)n, chunk, want)) break;
        size_t len = strnlen(chunk, want);
        memcpy(out + n, chunk, len);
        n += len;
        if (len < want) break;
    }
    out[n] = 0;
}

static void MERC_PLUGIN_CALL script_loaded(void *ud, MercHookContext *c)
{
    (void)ud;
    uint32_t object = c->ecx, L = 0;
    if (!host->read_guest(host->context, object + MF_OFF_LuaScript_luaState, &L, 4) || !L) return;
    EnterCriticalSection(&lock);
    unsigned slot = script_count < MAX_SCRIPTS ? script_count++ : (script_serial % MAX_SCRIPTS);
    scripts[slot].object = object;
    scripts[slot].L = L;
    scripts[slot].serial = ++script_serial;
    unsigned serial = script_serial;
    LeaveCriticalSection(&lock);
    InterlockedExchange(&scripts_dirty, 1);
    /* Note any text the script object points to (to learn where its name is). */
    char found[256] = "";
    for (uint32_t off = 0; off < 0x80; off += 4) {
        uint32_t ptr = 0;
        char text[33];
        if (!host->read_guest(host->context, object + off, &ptr, 4) || ptr < 0x10000 || ptr >= 0x4000000) continue;
        if (!host->read_guest(host->context, ptr, text, 32)) continue;
        text[32] = 0;
        size_t len = 0;
        while (len < 32 && text[len] >= 32 && text[len] < 127) len++;
        if (len >= 4 && text[len] == 0) {
            size_t have = strlen(found);
            snprintf(found + have, sizeof(found) - have, " +0x%02X=\"%s\"", off, text);
        }
    }
    char msg[400];
    snprintf(msg, sizeof(msg), "script #%u loaded: object 0x%08X, Lua state 0x%08X;%s", serial, object, L,
             found[0] ? found : " no text fields");
    host->log(host->context, msg);
}

static uint32_t lua_call(uint32_t fn, unsigned n, const uint32_t *args)
{
    MercGuestCall c;
    return guest(fn, 0, n, args, &c) ? c.eax : 0;
}

static void run_lua(int serial, const char *line)
{
    Script target = {0};
    EnterCriticalSection(&lock);
    for (unsigned i = 0; i < script_count; i++)
        if ((serial == 0 && scripts[i].serial > target.serial) || (serial > 0 && scripts[i].serial == (unsigned)serial))
            target = scripts[i];
    LeaveCriticalSection(&lock);
    if (!target.L) { append_output("> %s\r\n  no script loaded yet", line); return; }
    /* Only use a state the game still knows. */
    uint32_t args[4];
    args[0] = target.L;
    if (lua_call(MF_FN_LuaScript_FromState, 1, args) != target.object) {
        append_output("> %s\r\n  script #%u has been unloaded; pick another", line, target.serial);
        return;
    }
    uint32_t scratch, size;
    if (!host->guest_scratch(host->context, &scratch, &size) || size < 4096) { append_output("no scratch memory"); return; }
    static const char prefix[] =
        "local function __out(...) local s = '' for i = 1, arg.n do if i > 1 then s = s .. '   ' end "
        "s = s .. tostring(arg[i]) end return s end return __out(";
    char chunk[1400];
    const uint32_t name = scratch, text = scratch + 32;
    host->write_guest(host->context, name, "=console", 9);
    uint32_t top = lua_call(MF_FN_lua_gettop, 1, args);
    /* Try it as an expression (show its values), then as a statement. */
    int expression = 1;
    int len = snprintf(chunk, sizeof(chunk), "%s%s\n)", prefix, line);
    host->write_guest(host->context, text, chunk, (size_t)len + 1);
    uint32_t load[4] = {target.L, text, (uint32_t)len, name};
    uint32_t status = lua_call(MF_FN_luaL_loadbuffer, 4, load);
    if (status) {
        uint32_t settop[2] = {target.L, top};
        lua_call(MF_FN_lua_settop, 2, settop);
        expression = 0;
        len = snprintf(chunk, sizeof(chunk), "%s", line);
        host->write_guest(host->context, text, chunk, (size_t)len + 1);
        load[2] = (uint32_t)len;
        status = lua_call(MF_FN_luaL_loadbuffer, 4, load);
    }
    if (!status) {
        uint32_t pcall[4] = {target.L, 0, 1, 0};
        status = lua_call(MF_FN_lua_pcall, 4, pcall);
    }
    char result[1024];
    uint32_t tostr[2] = {target.L, (uint32_t)-1};
    read_text(lua_call(MF_FN_lua_tostring, 2, tostr), result, sizeof(result));
    uint32_t settop[2] = {target.L, top};
    lua_call(MF_FN_lua_settop, 2, settop);
    if (status) append_output("> %s\r\n  error: %s", line, result[0] ? result : "(no message)");
    else if (expression) append_output("> %s\r\n  = %s", line, result);
    else append_output("> %s\r\n  ok", line);
}

static int f7_down;
static double since_inspect;

static int MERC_PLUGIN_CALL on_update(void *ud, const MercPluginUpdate *u)
{
    (void)ud;
    /* F7 toggles the window while this game (or the console) has focus. */
    DWORD pid = 0;
    GetWindowThreadProcessId(GetForegroundWindow(), &pid);
    int down = (GetAsyncKeyState(VK_F7) & 0x8000) != 0;
    if (down && !f7_down && pid == GetCurrentProcessId() && wnd)
        PostMessageW(wnd, WM_APP_TOGGLE, 0, 0);
    f7_down = down;

    since_inspect += u->wall_delta_seconds;
    if (since_inspect >= 0.25) {
        since_inspect = 0;
        inspect();
    }
    if (!host->in_gameplay(host->context))
        return 1;

    int spawn;
    float money;
    unsigned scan;
    EnterCriticalSection(&lock);
    spawn = pending_spawn; pending_spawn = -1;
    money = pending_money; pending_money = 0;
    scan = scan_next;
    LeaveCriticalSection(&lock);
    if (spawn >= 0) do_spawn(spawn);
    char lua_line[1024];
    int lua_serial;
    EnterCriticalSection(&lock);
    lua_serial = pending_lua_serial;
    pending_lua_serial = -1;
    memcpy(lua_line, pending_lua, sizeof(lua_line));
    LeaveCriticalSection(&lock);
    if (lua_serial >= 0) run_lua(lua_serial, lua_line);
    if (money != 0) do_money(money);
    if (scan < template_count) {
        unsigned end = scan + 128 < template_count ? scan + 128 : template_count, loaded = 0;
        for (unsigned i = scan; i < end; i++)
            templates[i].loaded = template_loaded(templates[i].hash) ? 1 : 2;
        EnterCriticalSection(&lock);
        scan_next = end;
        LeaveCriticalSection(&lock);
        if (end == template_count) {
            for (unsigned i = 0; i < template_count; i++) loaded += templates[i].loaded == 1;
            set_status("%u of %u templates are loaded here.", loaded, template_count);
        }
        InterlockedExchange(&list_dirty, 1);
    }
    return 1;
}

/* ---------- window thread ---------- */

static int contains(const char *hay, const char *needle)
{
    size_t n = strlen(needle);
    if (!n) return 1;
    for (; *hay; hay++)
        if (_strnicmp(hay, needle, n) == 0) return 1;
    return 0;
}

static void fill_list(void)
{
    char search[64] = "", row[160];
    GetWindowTextA(search_edit, search, sizeof(search));
    int only_loaded = SendMessageW(loaded_check, BM_GETCHECK, 0, 0) == BST_CHECKED;
    SendMessageW(list_box, WM_SETREDRAW, FALSE, 0);
    SendMessageA(list_box, LB_RESETCONTENT, 0, 0);
    list_size = 0;
    for (unsigned i = 0; i < template_count; i++) {
        const Template *t = &templates[i];
        if (only_loaded && t->loaded != 1) continue;
        if (!contains(t->name, search) && !contains(t->type, search) && !contains(t->faction, search)) continue;
        snprintf(row, sizeof(row), "%s%s   (%s%s%s)", t->loaded == 1 ? "* " : "", t->name, t->type,
                 t->faction[0] ? ", " : "", t->faction);
        SendMessageA(list_box, LB_ADDSTRING, 0, (LPARAM)row);
        list_map[list_size++] = (int)i;
    }
    SendMessageW(list_box, WM_SETREDRAW, TRUE, 0);
    InvalidateRect(list_box, NULL, TRUE);
}

static void fill_scripts(void)
{
    char row[96];
    LRESULT sel = SendMessageW(script_combo, CB_GETCURSEL, 0, 0);
    unsigned keep = sel > 0 && sel <= MAX_SCRIPTS ? combo_serials[sel] : 0;
    SendMessageA(script_combo, CB_RESETCONTENT, 0, 0);
    SendMessageA(script_combo, CB_ADDSTRING, 0, (LPARAM)"Newest script");
    combo_serials[0] = 0;
    int choose = 0, n = 1;
    EnterCriticalSection(&lock);
    for (unsigned i = 0; i < script_count; i++) {
        snprintf(row, sizeof(row), "Script #%u (Lua state 0x%08X)", scripts[i].serial, scripts[i].L);
        SendMessageA(script_combo, CB_ADDSTRING, 0, (LPARAM)row);
        combo_serials[n] = scripts[i].serial;
        if (scripts[i].serial == keep) choose = n;
        n++;
    }
    LeaveCriticalSection(&lock);
    SendMessageW(script_combo, CB_SETCURSEL, (WPARAM)choose, 0);
}

static HWND control(const char *cls, const char *text, DWORD style, int x, int y, int w, int h, int id, HFONT font)
{
    HWND c = CreateWindowExA(cls[0] == 'E' || cls[0] == 'L' ? WS_EX_CLIENTEDGE : 0, cls, text,
                             WS_CHILD | WS_VISIBLE | style, x, y, w, h, wnd, (HMENU)(INT_PTR)id, NULL, NULL);
    SendMessageW(c, WM_SETFONT, (WPARAM)font, TRUE);
    return c;
}

static LRESULT CALLBACK window_proc(HWND h, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_APP_TOGGLE:
        ShowWindow(h, IsWindowVisible(h) ? SW_HIDE : SW_SHOWNOACTIVATE);
        return 0;
    case WM_CLOSE:
        ShowWindow(h, SW_HIDE);
        return 0;
    case WM_TIMER: {
        char info[1024], status[256];
        EnterCriticalSection(&lock);
        memcpy(info, info_text, sizeof(info));
        memcpy(status, status_text, sizeof(status));
        LeaveCriticalSection(&lock);
        char old[1024];
        GetWindowTextA(info_label, old, sizeof(old));
        if (strcmp(old, info)) SetWindowTextA(info_label, info);
        GetWindowTextA(status_label, old, sizeof(old));
        if (strcmp(old, status)) SetWindowTextA(status_label, status);
        if (InterlockedExchange(&list_dirty, 0)) fill_list();
        if (InterlockedExchange(&scripts_dirty, 0)) fill_scripts();
        if (InterlockedExchange(&output_dirty, 0)) {
            static char out[8192];
            EnterCriticalSection(&lock);
            memcpy(out, lua_output, sizeof(out));
            LeaveCriticalSection(&lock);
            SetWindowTextA(output_edit, out);
            SendMessageW(output_edit, EM_LINESCROLL, 0, 0x7FFF);
        }
        return 0;
    }
    case WM_COMMAND:
        switch (LOWORD(wp)) {
        case ID_SEARCH:
            if (HIWORD(wp) == EN_CHANGE) fill_list();
            return 0;
        case ID_LOADED:
            if (SendMessageW(loaded_check, BM_GETCHECK, 0, 0) == BST_CHECKED) {
                for (unsigned i = 0; i < template_count; i++) templates[i].loaded = 0;
                EnterCriticalSection(&lock);
                scan_next = 0;
                LeaveCriticalSection(&lock);
                set_status("Checking which templates are loaded here...");
            }
            fill_list();
            return 0;
        case ID_LIST:
            if (HIWORD(wp) != LBN_DBLCLK) return 0;
            /* fall through: double-click spawns */
        case ID_SPAWN: {
            LRESULT sel = SendMessageW(list_box, LB_GETCURSEL, 0, 0);
            if (sel == LB_ERR || (unsigned)sel >= list_size) { set_status("Pick a template first."); return 0; }
            EnterCriticalSection(&lock);
            pending_spawn = list_map[sel];
            LeaveCriticalSection(&lock);
            return 0;
        }
        case ID_RUN: {
            char line[1000];
            GetWindowTextA(lua_edit, line, sizeof(line));
            if (!line[0]) return 0;
            LRESULT sel = SendMessageW(script_combo, CB_GETCURSEL, 0, 0);
            EnterCriticalSection(&lock);
            snprintf(pending_lua, sizeof(pending_lua), "%s", line);
            pending_lua_serial = sel > 0 && sel <= MAX_SCRIPTS ? (int)combo_serials[sel] : 0;
            LeaveCriticalSection(&lock);
            SetWindowTextA(lua_edit, "");
            return 0;
        }
        case ID_MONEY:
            EnterCriticalSection(&lock);
            pending_money += 10000.0f;
            LeaveCriticalSection(&lock);
            return 0;
        }
        break;
    }
    return DefWindowProcW(h, msg, wp, lp);
}

static DWORD WINAPI window_thread(void *arg)
{
    (void)arg;
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = window_proc;
    wc.hInstance = GetModuleHandleW(NULL);
    wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1);
    wc.lpszClassName = L"FoundryConsole";
    RegisterClassW(&wc);
    wnd = CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_TOPMOST, wc.lpszClassName, L"Foundry Console (F7)",
                          WS_CAPTION | WS_SYSMENU, 40, 40, 520, 900, NULL, NULL, wc.hInstance, NULL);
    HFONT font = CreateFontW(-14, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Segoe UI");
    HFONT mono = CreateFontW(-13, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Consolas");
    control("STATIC", "Inspector", 0, 12, 8, 480, 18, 0, font);
    info_label = control("STATIC", "", SS_LEFT, 12, 28, 480, 150, 0, mono);
    control("STATIC", "Spawn a template (8 m ahead). Search:", 0, 12, 186, 480, 18, 0, font);
    search_edit = control("EDIT", "", ES_AUTOHSCROLL, 12, 206, 330, 24, ID_SEARCH, font);
    loaded_check = control("BUTTON", "Loaded here only", BS_AUTOCHECKBOX, 352, 208, 150, 20, ID_LOADED, font);
    list_box = control("LISTBOX", "", LBS_NOTIFY | WS_VSCROLL | LBS_NOINTEGRALHEIGHT, 12, 236, 480, 250, ID_LIST, mono);
    spawn_button = control("BUTTON", "Spawn", BS_DEFPUSHBUTTON, 12, 494, 100, 28, ID_SPAWN, font);
    money_button = control("BUTTON", "+$10,000", 0, 120, 494, 100, 28, ID_MONEY, font);
    status_label = control("STATIC", "", SS_LEFT, 12, 530, 480, 36, 0, font);
    control("STATIC", "Lua: run a line in a script's state (Enter runs it)", 0, 12, 574, 480, 18, 0, font);
    script_combo = control("COMBOBOX", "", CBS_DROPDOWNLIST | WS_VSCROLL, 12, 594, 480, 200, ID_SCRIPT, font);
    lua_edit = control("EDIT", "", ES_AUTOHSCROLL, 12, 626, 400, 24, ID_LUA, mono);
    run_button = control("BUTTON", "Run", 0, 418, 625, 74, 26, ID_RUN, font);
    output_edit = control("EDIT", "", ES_MULTILINE | ES_READONLY | ES_AUTOVSCROLL | WS_VSCROLL, 12, 658, 480, 190, 0, mono);
    fill_list();
    fill_scripts();
    SetTimer(wnd, ID_TIMER, 250, NULL);
    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) {
        if (msg.message == WM_KEYDOWN && msg.wParam == VK_RETURN && msg.hwnd == lua_edit) {
            SendMessageW(wnd, WM_COMMAND, MAKEWPARAM(ID_RUN, BN_CLICKED), (LPARAM)run_button);
            continue;
        }
        if (!IsDialogMessageW(wnd, &msg)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    return 0;
}

static void MERC_PLUGIN_CALL on_shutdown(void *ud)
{
    (void)ud;
    if (window_thread_id) PostThreadMessageW(window_thread_id, WM_QUIT, 0, 0);
}

MERC_PLUGIN_EXPORT int MERC_PLUGIN_CALL MercenariesPlugin_Init(
    const MercPluginHost *h, MercPlugin *plugin, char *error, uint32_t error_size)
{
    if (!MERC_PLUGIN_HOST_HAS(h, hook_guest)) {
        snprintf(error, error_size, "Foundry Console needs the Foundry port patches (plugin calls and hooks).");
        return 0;
    }
    host = h;
    InitializeCriticalSection(&lock);
    load_templates();
    if (!h->hook_guest(h->context, MF_FN_Spawner_SpawnTemplate, NULL, spawn_after, NULL))
        h->log(h->context, "spawn hook unavailable (run port-hooks); recent spawns won't show");
    if (!h->hook_guest(h->context, MF_FN_LuaScript_RegisterCommands, NULL, script_loaded, NULL))
        h->log(h->context, "script hook unavailable (run port-hooks); the Lua console can't find scripts");
    plugin->on_update = on_update;
    plugin->on_shutdown = on_shutdown;
    HANDLE t = CreateThread(NULL, 0, window_thread, NULL, 0, &window_thread_id);
    if (t) CloseHandle(t);
    char text[96];
    snprintf(text, sizeof(text), "ready: %u templates; press F7 in game", template_count);
    h->log(h->context, text);
    return 1;
}
