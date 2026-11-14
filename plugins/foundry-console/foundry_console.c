/* Foundry Console: a developer window for Mercenaries Recompiled (F7).
 *
 *   Inspector  - gameplay state, the player's position, heading, money and
 *                hit points, and the game's recent spawns.
 *   Spawn      - any object template by name (searchable list from
 *                templates.tsv, written by `python -m mercfoundry templates`),
 *                placed 8 m ahead of the player; "Loaded here only" asks the
 *                game which templates the current area has loaded.
 *   Nearby     - world objects within 100 m of the player, nearest first,
 *                named from objectnames.txt; the selected one's position,
 *                flags and properties as the game reports them; bring it
 *                here, raise or lower it, turn it, or remove it (the same
 *                engine calls as Actor_SetPosition, Actor_SetYaw and
 *                Actor_Remove).
 *   Lua        - run a line of Lua in a loaded script's state (an
 *                expression shows its values; a statement shows "ok").
 *   Live link  - when the Foundry editor runs (python -m mercfoundry edit,
 *                port 8740), the console tells it where the player is and
 *                carries out its live edits: move, turn or remove objects and
 *                teleport the player (see mercfoundry/editor/live.py). A
 *                thread polls the editor, so the game never waits on it.
 *   Sampler    - off unless asked for (foundry-console.ini, or
 *                Foundry_Sample(1) in the Lua box): a thread reads the port's
 *                current-function variable thousands of times a second and
 *                writes hit counts per label (Foundry_Label('tank')) to
 *                samples\<session>.tsv, for `funcmap profile`.
 *
 * While the console has focus it tells the game a tool window is open (as
 * the F9 panel does): the game leaves exclusive fullscreen so the window
 * shows, and ignores game input so typing doesn't move the player.
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
#include <winhttp.h>
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "winhttp.lib")

#define PLAYER0_HASH 0x660E4490u      /* HashName("player0") */
#define NAME_PROPERTY_HASH 0x8D39BDE6u /* HashName("name") */
#define MAX_TEMPLATES 4096
#define SPAWN_DISTANCE 8.0f
/* World object tables (see the function map): 16,384 object slots with their
 * handles, and 2,048 named objects (name hash -> handle). */
#define OBJECTS 0x00614070u
#define OBJECT_HANDLES 0x00624070u
#define OBJECT_SLOTS 0x4000u
#define NAME_HANDLES 0x0063DD70u
#define NAME_KEYS 0x0063FD70u
#define NAME_SLOTS 0x800u
#define WORLDOBJECT_POSITION 0x08u   /* float[3], then a quaternion at 0x14 */
#define WORLDOBJECT_ROTATION 0x14u
#define NEARBY_RADIUS 100.0f
/* The loaded terrain (RedTerrain globals, from the loader at 0x1F07A0):
 * unpacked height bytes, per-16x16-block {shift, base} entries, size, block
 * size, height step and the world-origin offsets. */
#define TERRAIN_HEIGHTS 0x0064326Cu
#define TERRAIN_BLOCKS 0x00643274u
#define TERRAIN_SIZE 0x00643310u
#define TERRAIN_BLOCK 0x00643300u
#define TERRAIN_STEP 0x00643290u
#define TERRAIN_SPACING 0x00643284u
#define TERRAIN_ORIGIN_X 0x0064327Cu
#define TERRAIN_ORIGIN_Z 0x00643278u
#define MAX_NEARBY 40
#define MAX_NAMES 8192
/* Editing (from Lua_Actor_SetPosition / SetYaw / Remove): objects with flag
 * bit 3 are plain world objects (transform through vtable slot 0); the rest
 * have a game actor at +0x24, edited through its vtable when its type bits
 * match g_HitPointActorType (value at +4, mask at +8). */
#define FLAG_PLAIN 0x08u
#define ACTOR_TYPE_VALUE (MF_VAR_g_HitPointActorType + 4u)
#define ACTOR_TYPE_MASK (MF_VAR_g_HitPointActorType + 8u)
#define ACTOR_VT_TYPE 0x04u
#define ACTOR_VT_REMOVE 0x10u
#define ACTOR_VT_SET_POSITION 0x64u
#define ACTOR_VT_SET_YAW 0x68u
enum { EDIT_NONE, EDIT_HERE, EDIT_UP, EDIT_DOWN, EDIT_LEFT, EDIT_RIGHT, EDIT_REMOVE };

typedef struct Template {
    char name[64], layer[40], type[24], faction[16];
    uint32_t hash;
    uint8_t loaded; /* 0 unknown, 1 loaded here, 2 not loaded */
} Template;

static const MercPluginHost *host;
/* The function sampler (near the end of the file). */
static volatile LONG sample_playing;      /* in_gameplay, for the function sampler's thread */
static int sampler_status(char *out, size_t size);
static void sampler_init(void);
static void sampler_shutdown(void);
static int MERC_PLUGIN_CALL cmd_sample(void *ud, uint32_t L);
static int MERC_PLUGIN_CALL cmd_label(void *ud, uint32_t L);
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

/* Nearby objects */
typedef struct Nearby { uint32_t object, handle; float distance, pos[3]; char name[48], model[48]; } Nearby;
static Nearby nearby[MAX_NEARBY];
static unsigned nearby_count;
static uint32_t selected_handle;
static char details_text[1024] = "Select an object.";
static volatile LONG nearby_dirty, details_dirty;
static int pending_edit;
static uint32_t pending_edit_handle;
typedef struct Name { uint32_t hash; char text[48]; } Name;
static Name names[MAX_NAMES];
static unsigned name_count;

/* Window */
static DWORD window_thread_id;
static HWND game_window;            /* foreground window when the console was opened */
static volatile LONG want_tool;    /* 1 while the console has focus */
static int tool_state;
static HWND wnd, info_label, search_edit, list_box, loaded_check, spawn_button, money_button, status_label;
static HWND script_combo, lua_edit, run_button, output_edit;
static HWND nearby_list, details_label;
static HWND edit_buttons[6];
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
#define ID_NEARBY 110
#define ID_EDIT 120 /* + EDIT_HERE .. EDIT_REMOVE */

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

static void add_name(const char *text, size_t n)
{
    if (name_count >= MAX_NAMES || !n) return;
    Name *e = &names[name_count++];
    field(e->text, sizeof(e->text), text, n);
    e->hash = hash_name(e->text);
}

static int name_order(const void *a, const void *b)
{
    uint32_t x = ((const Name *)a)->hash, y = ((const Name *)b)->hash;
    return x < y ? -1 : x > y;
}

static void load_names(void)
{
    char path[MAX_PATH * 3], line[256];
    wchar_t wpath[MAX_PATH * 2];
    add_name("player0", 7);
    snprintf(path, sizeof(path), "%s\\objectnames.txt", host->plugin_directory);
    if (MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, MAX_PATH * 2)) {
        FILE *f = _wfopen(wpath, L"rb");
        if (f) {
            while (fgets(line, sizeof(line), f)) add_name(line, strcspn(line, "\r\n"));
            fclose(f);
        }
    }
    for (unsigned i = 0; i < template_count; i++) add_name(templates[i].name, strlen(templates[i].name));
    qsort(names, name_count, sizeof(Name), name_order);
}

static const char *name_for_hash(uint32_t hash)
{
    unsigned lo = 0, hi = name_count;
    while (lo < hi) {
        unsigned mid = (lo + hi) / 2;
        if (names[mid].hash < hash) lo = mid + 1;
        else hi = mid;
    }
    return lo < name_count && names[lo].hash == hash ? names[lo].text : NULL;
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

/* Height of the loaded terrain at grid cell (col, row), from the game's own
 * buffers (the formula of its height lookup at 0x146A10). */
static int terrain_units(int col, int row, float *out)
{
    uint32_t heights = 0, blocks = 0;
    int16_t size = 0, block = 0;
    float step = 0;
    host->read_guest(host->context, TERRAIN_HEIGHTS, &heights, 4);
    host->read_guest(host->context, TERRAIN_BLOCKS, &blocks, 4);
    host->read_guest(host->context, TERRAIN_SIZE, &size, 2);
    host->read_guest(host->context, TERRAIN_BLOCK, &block, 2);
    host->read_guest(host->context, TERRAIN_STEP, &step, 4);
    if (!heights || !blocks || size <= 0 || block <= 0) return 0;
    col &= size - 1;
    row &= size - 1;
    int bpr = size / block, blk = (row / block) * bpr + col / block;
    uint8_t byte = 0, shift = 0;
    int16_t base = 0;
    host->read_guest(host->context, heights + (uint32_t)(blk * block * block + (row % block) * block + col % block), &byte, 1);
    host->read_guest(host->context, blocks + (uint32_t)blk * 4, &shift, 1);
    host->read_guest(host->context, blocks + (uint32_t)blk * 4 + 2, &base, 2);
    *out = (float)(((int)byte << (shift & 31)) + base) * step;
    return 1;
}

/* "Surface: snow (texture slot 0)" for world x, z, asked of the game itself:
 * Terrain_GetSurfaceAt for the surface the level config gave the texture
 * there, and Terrain_GetTextureSlot (with the same cell rounding) for which
 * texture that is (0-15 ground layers, 16-31 decal textures). */
#define TERRAIN_OBJECT 0x00643370u
#define TERRAIN_INV_SPACING 0x00643280u
#define TERRAIN_CELL_ROUND 0x002DC340u
static const char *surface_line(float x, float z)
{
    static const char *names[] = {"none", "dirt", "road", "snow", "grass", "rock"};
    static char line[96];
    MercGuestCall c;
    uint32_t args[2];
    memcpy(&args[0], &x, 4);
    memcpy(&args[1], &z, 4);
    if (!guest(MF_FN_Terrain_GetSurfaceAt, 0, 2, args, &c)) return "";
    uint32_t surface = c.eax;
    float ox = 0, oz = 0, inv = 0, round = 0;
    host->read_guest(host->context, TERRAIN_ORIGIN_X, &ox, 4);
    host->read_guest(host->context, TERRAIN_ORIGIN_Z, &oz, 4);
    host->read_guest(host->context, TERRAIN_INV_SPACING, &inv, 4);
    host->read_guest(host->context, TERRAIN_CELL_ROUND, &round, 4);
    uint32_t cell[2] = {(uint32_t)(int)((x + ox) * inv + round), (uint32_t)(int)((z + oz) * inv + round)};
    int slot = guest(MF_FN_Terrain_GetTextureSlot, TERRAIN_OBJECT, 2, cell, &c) ? (int)c.eax : -1;
    snprintf(line, sizeof(line), "Surface: %s (texture slot %d)\r\n",
             surface < 6 ? names[surface] : "?", slot);
    return line;
}

/* Logs the loaded terrain's layout and its surface per texture slot (as the
 * game read them from the level config) whenever they change, e.g. once the
 * level after the intro has loaded. */
static void terrain_log_changes(void)
{
    static int32_t last[32];
    static int logged;
    int32_t now[32];
    host->read_guest(host->context, MF_VAR_g_TerrainSurfaces, now, sizeof(now));
    if (logged && !memcmp(now, last, sizeof(now))) return;
    logged = 1;
    memcpy(last, now, sizeof(now));
    float ox = 0, oz = 0, spacing = 0;
    int16_t size = 0;
    host->read_guest(host->context, TERRAIN_ORIGIN_X, &ox, 4);
    host->read_guest(host->context, TERRAIN_ORIGIN_Z, &oz, 4);
    host->read_guest(host->context, TERRAIN_SPACING, &spacing, 4);
    host->read_guest(host->context, TERRAIN_SIZE, &size, 2);
    char text[160];
    snprintf(text, sizeof(text), "terrain loaded: %d x %d cells of %.1f m; world x + %.1f and z + %.1f give the grid position",
             size, size, spacing, ox, oz);
    host->log(host->context, text);
    char list[320];
    int n = snprintf(list, sizeof(list), "terrain surfaces by slot (0 none, 1 dirt, 2 road, 3 snow, 4 grass, 5 rock):");
    for (int i = 0; i < 32 && n < (int)sizeof(list) - 4; i++)
        n += snprintf(list + n, sizeof(list) - n, " %d", now[i]);
    host->log(host->context, list);
}

static void inspect(void)
{
    char text[1024];
    int n = 0;
    int playing = host->in_gameplay(host->context);
    n += snprintf(text + n, sizeof(text) - n, "Gameplay: %s\r\n", playing ? "running" : "not running");
    n += sampler_status(text + n, sizeof(text) - n);
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
        float spacing = 0, ox = 0, oz = 0, ground;
        host->read_guest(host->context, TERRAIN_SPACING, &spacing, 4);
        host->read_guest(host->context, TERRAIN_ORIGIN_X, &ox, 4);
        host->read_guest(host->context, TERRAIN_ORIGIN_Z, &oz, 4);
        if (spacing > 0) {
            int col = (int)floorf((m[12] + ox) / spacing + 0.5f), row = (int)floorf((m[14] + oz) / spacing + 0.5f);
            if (terrain_units(col, row, &ground))
                n += snprintf(text + n, sizeof(text) - n, "Ground (terrain cell %d, %d): %.1f m\r\n", col, row, ground);
            n += snprintf(text + n, sizeof(text) - n, "%s", surface_line(m[12], m[14]));
        }
        terrain_log_changes();
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

static uint32_t slot_objects[OBJECT_SLOTS], slot_handles[OBJECT_SLOTS];
static uint32_t name_keys[NAME_SLOTS], name_handles[NAME_SLOTS];

/* Model names for unnamed objects, from the object's own property lookup
 * (vtable +0x0C, "GeometryFile"), cached by handle. */
#define MODEL_CACHE 512
static struct { uint32_t handle, object; char model[48]; } model_cache[MODEL_CACHE];

static void object_model(uint32_t object, uint32_t handle, char *out, size_t size)
{
    unsigned slot = (handle * 2654435761u) % MODEL_CACHE;
    if (model_cache[slot].handle == handle && model_cache[slot].object == object) {
        snprintf(out, size, "%s", model_cache[slot].model);
        return;
    }
    out[0] = 0;
    uint32_t vtable = 0, getter = 0, hash = hash_name("GeometryFile");
    MercGuestCall c;
    if (host->read_guest(host->context, object, &vtable, 4) && vtable &&
        host->read_guest(host->context, vtable + 0x0C, &getter, 4) && getter &&
        guest(getter, object, 1, &hash, &c) && c.eax)
        read_text(c.eax, out, size);
    model_cache[slot].handle = handle;
    model_cache[slot].object = object;
    snprintf(model_cache[slot].model, sizeof(model_cache[slot].model), "%s", out);
}

static int nearby_order(const void *a, const void *b)
{
    float x = ((const Nearby *)a)->distance, y = ((const Nearby *)b)->distance;
    return x < y ? -1 : x > y;
}

static void scan_nearby(void)
{
    uint32_t player = find_player();
    float m[16];
    if (!player) return;
    host->read_guest(host->context, player + MF_OFF_Actor_transform, m, sizeof(m));
    if (!host->read_guest(host->context, OBJECTS, slot_objects, sizeof(slot_objects)) ||
        !host->read_guest(host->context, OBJECT_HANDLES, slot_handles, sizeof(slot_handles)))
        return;
    host->read_guest(host->context, NAME_KEYS, name_keys, sizeof(name_keys));
    host->read_guest(host->context, NAME_HANDLES, name_handles, sizeof(name_handles));
    static Nearby found[OBJECT_SLOTS];
    unsigned n = 0;
    for (unsigned i = 0; i < OBJECT_SLOTS; i++) {
        uint32_t obj = slot_objects[i];
        unsigned char head[0x34];
        if (!obj || !host->read_guest(host->context, obj, head, sizeof(head))) continue;
        uint16_t flags;
        float pos[3];
        memcpy(&flags, head + MF_OFF_WorldObject_flags, 2);
        if (!(flags & 1) || (flags & 4)) continue; /* active, not removed */
        memcpy(pos, head + WORLDOBJECT_POSITION, 12);
        float dx = pos[0] - m[12], dy = pos[1] - m[13], dz = pos[2] - m[14];
        float d = sqrtf(dx * dx + dy * dy + dz * dz);
        if (!(d <= NEARBY_RADIUS)) continue;
        Nearby *e = &found[n++];
        e->object = obj;
        memcpy(&e->handle, head + MF_OFF_WorldObject_handle, 4);
        e->distance = d;
        memcpy(e->pos, pos, 12);
        e->name[0] = 0;
        e->model[0] = 0;
    }
    qsort(found, n, sizeof(Nearby), nearby_order);
    if (n > MAX_NEARBY) n = MAX_NEARBY;
    /* Names: the named-object table maps name hashes to handles. */
    for (unsigned k = 0; k < n; k++) {
        for (unsigned j = 0; j < NAME_SLOTS; j++) {
            if (name_keys[j] && name_handles[j] == found[k].handle) {
                const char *text = name_for_hash(name_keys[j]);
                if (text) snprintf(found[k].name, sizeof(found[k].name), "%s", text);
                else snprintf(found[k].name, sizeof(found[k].name), "name 0x%08X", name_keys[j]);
                break;
            }
        }
        if (!found[k].name[0]) object_model(found[k].object, found[k].handle, found[k].model, sizeof(found[k].model));
    }
    EnterCriticalSection(&lock);
    memcpy(nearby, found, n * sizeof(Nearby));
    nearby_count = n;
    LeaveCriticalSection(&lock);
    InterlockedExchange(&nearby_dirty, 1);
}

static void describe_selected(void)
{
    static const char *props[] = {"objectType", "displayName", "faction", "hitPoints", "GeometryFile",
                                  "AnimationTable", "path", "squad"};
    uint32_t handle;
    Nearby e = {0};
    EnterCriticalSection(&lock);
    handle = selected_handle;
    for (unsigned i = 0; i < nearby_count; i++)
        if (nearby[i].handle == handle) e = nearby[i];
    LeaveCriticalSection(&lock);
    if (!handle) return;
    char text[1024];
    int n = 0;
    if (!e.object) {
        n = snprintf(text, sizeof(text), "Handle 0x%08X is no longer nearby.", handle);
    } else {
        unsigned char head[0x34];
        float q[4];
        uint16_t flags = 0;
        host->read_guest(host->context, e.object, head, sizeof(head));
        memcpy(&flags, head + MF_OFF_WorldObject_flags, 2);
        memcpy(q, head + WORLDOBJECT_ROTATION, 16);
        /* Heading from the quaternion's forward (+z) axis. */
        float fx = 2.0f * (q[0] * q[2] + q[3] * q[1]), fz = 1.0f - 2.0f * (q[0] * q[0] + q[1] * q[1]);
        double heading = atan2(fx, fz) * 180.0 / 3.14159265358979;
        if (heading < 0) heading += 360.0;
        n += snprintf(text + n, sizeof(text) - n,
                      "%s\r\nObject 0x%08X  handle 0x%08X  flags 0x%04X\r\n"
                      "Position %.1f, %.1f, %.1f  (%.1f m away)\r\nHeading %.0f degrees\r\n",
                      e.name[0] ? e.name : e.model[0] ? e.model : "(unnamed)", e.object, e.handle, flags,
                      e.pos[0], e.pos[1], e.pos[2], e.distance, heading);
        /* Properties, from the object's own lookup (vtable +0x0C, by name hash). */
        uint32_t vtable = 0, getter = 0;
        host->read_guest(host->context, e.object, &vtable, 4);
        if (vtable) host->read_guest(host->context, vtable + 0x0C, &getter, 4);
        for (unsigned i = 0; getter && i < sizeof(props) / sizeof(props[0]); i++) {
            MercGuestCall c;
            uint32_t hash = hash_name(props[i]);
            char value[96];
            if (!guest(getter, e.object, 1, &hash, &c) || !c.eax) continue;
            read_text(c.eax, value, sizeof(value));
            if (value[0]) n += snprintf(text + n, sizeof(text) - n, "%s = %s\r\n", props[i], value);
        }
    }
    EnterCriticalSection(&lock);
    memcpy(details_text, text, sizeof(details_text));
    LeaveCriticalSection(&lock);
    InterlockedExchange(&details_dirty, 1);
}

static uint32_t vcall(uint32_t self, uint32_t slot, unsigned n, const uint32_t *args, int *ok)
{
    uint32_t vtable = 0, fn = 0;
    MercGuestCall c;
    *ok = host->read_guest(host->context, self, &vtable, 4) && vtable &&
          host->read_guest(host->context, vtable + slot, &fn, 4) && fn && guest(fn, self, n, args, &c);
    return *ok ? c.eax : 0;
}

static void do_edit(int kind, uint32_t handle)
{
    MercGuestCall c;
    uint32_t obj = 0, scratch, size;
    int ok;
    if (guest(MF_FN_World_ObjectFromHandle, 0, 1, &handle, &c)) obj = c.eax;
    if (!obj) { set_status("That object is gone."); return; }
    if (!host->guest_scratch(host->context, &scratch, &size) || size < 256) { set_status("No scratch memory."); return; }
    uint16_t flags = 0;
    float pos[3], q[4];
    host->read_guest(host->context, obj + MF_OFF_WorldObject_flags, &flags, 2);
    host->read_guest(host->context, obj + WORLDOBJECT_POSITION, pos, 12);
    host->read_guest(host->context, obj + WORLDOBJECT_ROTATION, q, 16);
    float fx = 2.0f * (q[0] * q[2] + q[3] * q[1]), fz = 1.0f - 2.0f * (q[0] * q[0] + q[1] * q[1]);
    float yaw = atan2f(fx, fz);
    if (kind == EDIT_HERE) {
        uint32_t player = find_player();
        float m[16];
        if (!player) return;
        host->read_guest(host->context, player + MF_OFF_Actor_transform, m, sizeof(m));
        pos[0] = m[12] + m[8] * 3.0f;
        pos[1] = m[13];
        pos[2] = m[14] + m[10] * 3.0f;
    }
    if (kind == EDIT_UP) pos[1] += 1.0f;
    if (kind == EDIT_DOWN) pos[1] -= 1.0f;
    if (kind == EDIT_LEFT) yaw -= 3.14159265f / 4;
    if (kind == EDIT_RIGHT) yaw += 3.14159265f / 4;

    if (flags & FLAG_PLAIN) {
        if (kind == EDIT_REMOVE) {
            guest(MF_FN_WorldObject_Remove, obj, 0, NULL, &c);
            set_status("Removed.");
            return;
        }
        /* Rebuild the transform (yaw about the vertical axis) and set it. */
        float m[16] = {cosf(yaw), 0, -sinf(yaw), 0, 0, 1, 0, 0, sinf(yaw), 0, cosf(yaw), 0, pos[0], pos[1], pos[2], 1};
        host->write_guest(host->context, scratch, m, sizeof(m));
        vcall(obj, 0x00, 1, &scratch, &ok);
        set_status(ok ? "Moved." : "Couldn't move it.");
        return;
    }
    uint32_t actor = 0, value = 0, mask = 0;
    host->read_guest(host->context, obj + MF_OFF_WorldObject_actor, &actor, 4);
    host->read_guest(host->context, ACTOR_TYPE_VALUE, &value, 4);
    host->read_guest(host->context, ACTOR_TYPE_MASK, &mask, 4);
    uint32_t bits = actor ? vcall(actor, ACTOR_VT_TYPE, 0, NULL, &ok) : 0;
    if (!actor || !ok || (bits & mask) != value) {
        set_status("The game doesn't edit this kind of object this way.");
        return;
    }
    if (kind == EDIT_REMOVE) {
        vcall(actor, ACTOR_VT_REMOVE, 0, NULL, &ok);
        set_status(ok ? "Removed." : "Couldn't remove it.");
    } else if (kind == EDIT_LEFT || kind == EDIT_RIGHT) {
        uint32_t bitsyaw;
        memcpy(&bitsyaw, &yaw, 4);
        vcall(actor, ACTOR_VT_SET_YAW, 1, &bitsyaw, &ok);
        set_status(ok ? "Turned." : "Couldn't turn it.");
    } else {
        host->write_guest(host->context, scratch, pos, 12);
        vcall(actor, ACTOR_VT_SET_POSITION, 1, &scratch, &ok);
        set_status(ok ? "Moved." : "Couldn't move it.");
    }
}

static double since_nearby;

static int f7_down;
static double since_inspect;

/* In-game panel (F8): the inspector text drawn into the game's own frames
 * through the host's set_overlay, so it shows in exclusive fullscreen too. */
#define HUD_W 420
#define HUD_MAX_H 380
#define HUD_LINE 15
static int f8_down, hud_on;
/* Typing Lua in the panel (` starts, Enter runs, Esc stops): keys come from
 * the host's set_text_input / read_text_input, which keep them from the game. */
static int tilde_down, typing;
static char typed[256], last_typed[256];
static HDC hud_dc;
static HBITMAP hud_bitmap;
static HFONT hud_font;
static uint32_t *hud_pixels;

static void hud_hide(void)
{
    if (MERC_PLUGIN_HOST_HAS(host, set_overlay))
        host->set_overlay(host->context, NULL, 0, 0, 0, 0);
}

/* The panel's text: the inspector, then (once Lua has been used here) the
 * last lines of Lua output and the input line. */
static void hud_text(char *out, size_t size)
{
    EnterCriticalSection(&lock);
    int n = snprintf(out, size, "%s", info_text);
    if (typing || lua_output[0]) {
        /* last 6 lines of the output */
        const char *p = lua_output + strlen(lua_output);
        int lines = 0;
        while (p > lua_output && lines < 7) { p--; if (*p == '\n') lines++; }
        if (*p == '\n') p++;
        n += snprintf(out + n, size - n, "\r\n----\r\n%s", p);
    }
    LeaveCriticalSection(&lock);
    if (typing && n < (int)size)
        snprintf(out + n, size - n, "%s> %s_", n && out[n - 1] != '\n' ? "\r\n" : "", typed);
}

static void hud_draw(const char *text);

/* Keys typed since the last update: edit the line, run it, or stop typing.
 * Returns 1 if anything changed. */
static int hud_typing_keys(void)
{
    if (!typing || !MERC_PLUGIN_HOST_HAS(host, read_text_input)) return 0;
    uint32_t keys[64];
    int count = host->read_text_input(host->context, keys, 64), changed = 0;
    for (int i = 0; i < count; i++) {
        uint32_t k = keys[i];
        size_t len = strlen(typed);
        changed = 1;
        if (k == (MERC_KEY_FLAG | VK_ESCAPE)) {
            typing = 0;
            host->set_text_input(host->context, 0);
            return 1;
        } else if (k == (MERC_KEY_FLAG | VK_RETURN)) {
            if (len) {
                memcpy(last_typed, typed, sizeof(typed));
                run_lua(0, typed);
                typed[0] = 0;
            }
        } else if (k == (MERC_KEY_FLAG | VK_BACK)) {
            if (len) typed[len - 1] = 0;
        } else if (k == (MERC_KEY_FLAG | VK_UP)) {
            memcpy(typed, last_typed, sizeof(typed));
        } else if (!(k & MERC_KEY_FLAG) && k < 128) {
            if (k == '`' && !len) continue;          /* the key that opened typing */
            if (len + 1 < sizeof(typed)) { typed[len] = (char)k; typed[len + 1] = 0; }
        }
    }
    return changed;
}

static void hud_draw(const char *text)
{
    if (!MERC_PLUGIN_HOST_HAS(host, set_overlay)) return;
    if (!hud_dc) {
        BITMAPINFO bi = {0};
        bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
        bi.bmiHeader.biWidth = HUD_W;
        bi.bmiHeader.biHeight = -HUD_MAX_H;
        bi.bmiHeader.biPlanes = 1;
        bi.bmiHeader.biBitCount = 32;
        bi.bmiHeader.biCompression = BI_RGB;
        hud_dc = CreateCompatibleDC(NULL);
        hud_bitmap = CreateDIBSection(hud_dc, &bi, DIB_RGB_COLORS, (void **)&hud_pixels, NULL, 0);
        if (!hud_bitmap || !hud_pixels) { hud_on = 0; return; }
        SelectObject(hud_dc, hud_bitmap);
        hud_font = CreateFontA(-13, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, ANTIALIASED_QUALITY, 0, "Consolas");
        SelectObject(hud_dc, hud_font);
        SetBkMode(hud_dc, TRANSPARENT);
        SetTextColor(hud_dc, RGB(235, 240, 242));
    }
    memset(hud_pixels, 0, HUD_W * HUD_MAX_H * 4);
    int y = 6;
    const char *title = typing ? "Foundry Console  (Enter runs, Esc stops typing)"
                               : "Foundry Console  (` types Lua, F8 hides, F7 window)";
    TextOutA(hud_dc, 8, y, title, (int)strlen(title));
    y += HUD_LINE + 4;
    const char *p = text;
    while (*p && y + HUD_LINE <= HUD_MAX_H - 4) {
        const char *end = strpbrk(p, "\r\n");
        int len = end ? (int)(end - p) : (int)strlen(p);
        if (len) TextOutA(hud_dc, 8, y, p, len);
        y += HUD_LINE;
        if (!end) break;
        p = end;
        if (*p == '\r') p++;
        if (*p == '\n') p++;
    }
    GdiFlush();
    int h = y + 6;
    /* GDI writes grey text on black. Make it premultiplied BGRA: a dark,
     * mostly opaque panel (alpha 170) with the text on top. */
    for (int i = 0; i < HUD_W * h; i++) {
        uint32_t c = hud_pixels[i];
        unsigned t = c & 255, g = (c >> 8) & 255, r = (c >> 16) & 255;
        if (g > t) t = g;
        if (r > t) t = r;
        unsigned a = 170 + t * 85 / 255;
        unsigned bg = 14 * (255 - t) / 255;          /* premultiplied panel colour */
        unsigned v = t + bg;
        hud_pixels[i] = (a << 24) | (v << 16) | (v << 8) | (v + 4 * (255 - t) / 255);
    }
    host->set_overlay(host->context, hud_pixels, HUD_W, (uint32_t)h, 12, 150);
}

static void live_update(double dt);
static void live_report_loaded(void);

static int MERC_PLUGIN_CALL on_update(void *ud, const MercPluginUpdate *u)
{
    (void)ud;
    InterlockedExchange(&sample_playing, host->in_gameplay(host->context) != 0);
    live_update(u->wall_delta_seconds);
    /* F7 toggles the window while this game (or the console) has focus. */
    DWORD pid = 0;
    GetWindowThreadProcessId(GetForegroundWindow(), &pid);
    int down = (GetAsyncKeyState(VK_F7) & 0x8000) != 0;
    if (down && !f7_down && pid == GetCurrentProcessId() && wnd)
        PostMessageW(wnd, WM_APP_TOGGLE, 0, 0);
    f7_down = down;
    int f8 = (GetAsyncKeyState(VK_F8) & 0x8000) != 0;
    if (f8 && !f8_down && pid == GetCurrentProcessId()) {
        hud_on = !hud_on;
        since_inspect = 1.0;                 /* draw (or clear) right away */
        if (!hud_on) {
            if (typing) { typing = 0; host->set_text_input(host->context, 0); }
            hud_hide();
        }
    }
    f8_down = f8;
    int tilde = (GetAsyncKeyState(VK_OEM_3) & 0x8000) != 0;
    if (tilde && !tilde_down && hud_on && !typing && pid == GetCurrentProcessId() &&
        MERC_PLUGIN_HOST_HAS(host, set_text_input) && host->set_text_input(host->context, 1)) {
        typing = 1;
        typed[0] = 0;
        since_inspect = 1.0;
    }
    tilde_down = tilde;
    if (hud_typing_keys())
        since_inspect = 1.0;                 /* show the keystroke now */
    LONG want = InterlockedCompareExchange(&want_tool, 0, 0);
    if (want != tool_state && MERC_PLUGIN_HOST_HAS(host, set_tool_window)) {
        host->set_tool_window(host->context, want);
        tool_state = want;
    }

    since_inspect += u->wall_delta_seconds;
    if (since_inspect >= 0.25) {
        since_inspect = 0;
        inspect();
        if (hud_on) {
            char text[2048];
            hud_text(text, sizeof(text));
            hud_draw(text);
        }
    }
    if (!host->in_gameplay(host->context))
        return 1;
    since_nearby += u->wall_delta_seconds;
    if (since_nearby >= 0.5 && wnd && IsWindowVisible(wnd)) {
        since_nearby = 0;
        scan_nearby();
        describe_selected();
    }

    int spawn;
    float money;
    unsigned scan;
    EnterCriticalSection(&lock);
    spawn = pending_spawn; pending_spawn = -1;
    money = pending_money; pending_money = 0;
    scan = scan_next;
    LeaveCriticalSection(&lock);
    if (spawn >= 0) do_spawn(spawn);
    int edit;
    uint32_t edit_handle;
    EnterCriticalSection(&lock);
    edit = pending_edit; pending_edit = EDIT_NONE;
    edit_handle = pending_edit_handle;
    LeaveCriticalSection(&lock);
    if (edit != EDIT_NONE && edit_handle) {
        do_edit(edit, edit_handle);
        since_nearby = 1.0; /* refresh the list and details right away */
    }
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
            live_report_loaded();
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

static void fill_nearby(void)
{
    char row[128];
    uint32_t keep;
    int choose = -1;
    SendMessageW(nearby_list, WM_SETREDRAW, FALSE, 0);
    LRESULT top = SendMessageW(nearby_list, LB_GETTOPINDEX, 0, 0);
    SendMessageA(nearby_list, LB_RESETCONTENT, 0, 0);
    EnterCriticalSection(&lock);
    keep = selected_handle;
    for (unsigned i = 0; i < nearby_count; i++) {
        const Nearby *e = &nearby[i];
        if (e->name[0]) snprintf(row, sizeof(row), "%5.1f m  %s", e->distance, e->name);
        else if (e->model[0]) snprintf(row, sizeof(row), "%5.1f m  [%s]", e->distance, e->model);
        else snprintf(row, sizeof(row), "%5.1f m  handle 0x%08X", e->distance, e->handle);
        SendMessageA(nearby_list, LB_ADDSTRING, 0, (LPARAM)row);
        if (e->handle == keep) choose = (int)i;
    }
    LeaveCriticalSection(&lock);
    if (choose >= 0) SendMessageW(nearby_list, LB_SETCURSEL, (WPARAM)choose, 0);
    if (top != LB_ERR) SendMessageW(nearby_list, LB_SETTOPINDEX, (WPARAM)top, 0);
    SendMessageW(nearby_list, WM_SETREDRAW, TRUE, 0);
    InvalidateRect(nearby_list, NULL, TRUE);
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
        if (IsWindowVisible(h) && GetForegroundWindow() == h) {
            ShowWindow(h, SW_HIDE);
            if (game_window) SetForegroundWindow(game_window);
        } else {
            HWND fg = GetForegroundWindow();
            if (fg && fg != h) game_window = fg;
            ShowWindow(h, SW_SHOW);
            SetForegroundWindow(h);
        }
        return 0;
    case WM_ACTIVATE:
        InterlockedExchange(&want_tool, LOWORD(wp) != WA_INACTIVE);
        break;
    case WM_CLOSE:
        ShowWindow(h, SW_HIDE);
        InterlockedExchange(&want_tool, 0);
        if (game_window) SetForegroundWindow(game_window);
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
        if (InterlockedExchange(&nearby_dirty, 0)) fill_nearby();
        if (InterlockedExchange(&details_dirty, 0)) {
            char d[1024];
            EnterCriticalSection(&lock);
            memcpy(d, details_text, sizeof(d));
            LeaveCriticalSection(&lock);
            SetWindowTextA(details_label, d);
        }
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
        case ID_NEARBY:
            if (HIWORD(wp) == LBN_SELCHANGE) {
                LRESULT sel = SendMessageW(nearby_list, LB_GETCURSEL, 0, 0);
                EnterCriticalSection(&lock);
                if (sel != LB_ERR && (unsigned)sel < nearby_count) selected_handle = nearby[sel].handle;
                LeaveCriticalSection(&lock);
            }
            return 0;
        case ID_EDIT + EDIT_HERE: case ID_EDIT + EDIT_UP: case ID_EDIT + EDIT_DOWN:
        case ID_EDIT + EDIT_LEFT: case ID_EDIT + EDIT_RIGHT: case ID_EDIT + EDIT_REMOVE:
            EnterCriticalSection(&lock);
            pending_edit_handle = selected_handle;
            pending_edit = LOWORD(wp) - ID_EDIT;
            LeaveCriticalSection(&lock);
            if (!selected_handle) set_status("Select an object first.");
            return 0;
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
                          WS_CAPTION | WS_SYSMENU, 40, 40, 1040, 900, NULL, NULL, wc.hInstance, NULL);
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
    control("STATIC", "Nearby objects (100 m, nearest first)", 0, 520, 8, 480, 18, 0, font);
    nearby_list = control("LISTBOX", "", LBS_NOTIFY | WS_VSCROLL | LBS_NOINTEGRALHEIGHT, 520, 28, 490, 420, ID_NEARBY, mono);
    details_label = control("STATIC", "Select an object.", SS_LEFT, 520, 456, 490, 330, 0, mono);
    static const char *edit_labels[6] = {"Bring here", "Up 1 m", "Down 1 m", "Turn -45", "Turn +45", "Remove"};
    for (int i = 0; i < 6; i++)
        edit_buttons[i] = control("BUTTON", edit_labels[i], 0, 520 + i * 82, 800, 78, 28, ID_EDIT + EDIT_HERE + i, font);
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

/* ---------- live link with the Foundry editor ---------- */

#define LIVE_PORT 8740
#define LIVE_LINES 64
#define LIVE_LINE 640
static char live_cmds[LIVE_LINES][LIVE_LINE];
static unsigned live_cmd_count;
static char live_state[256];          /* player and gameplay lines, from the game thread */
static char live_results[32768];      /* result lines for the next poll */
static volatile LONG live_stop, live_connected;
static double since_live;

/* The live thread: post the game's state, take back the editor's commands. */
static DWORD WINAPI live_thread(void *arg)
{
    (void)arg;
    HINTERNET session = WinHttpOpen(L"FoundryConsole/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
                                    WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!session) return 0;
    WinHttpSetTimeouts(session, 500, 500, 1000, 1000);
    HINTERNET conn = WinHttpConnect(session, L"127.0.0.1", LIVE_PORT, 0);
    while (!InterlockedCompareExchange(&live_stop, 0, 0)) {
        static char body[33280];
        EnterCriticalSection(&lock);
        int n = snprintf(body, sizeof(body), "%s%s", live_state, live_results);
        live_results[0] = 0;
        LeaveCriticalSection(&lock);
        int ok = 0;
        HINTERNET req = conn ? WinHttpOpenRequest(conn, L"POST", L"/api/live/poll", NULL, WINHTTP_NO_REFERER,
                                                  WINHTTP_DEFAULT_ACCEPT_TYPES, 0) : NULL;
        if (req && WinHttpSendRequest(req, L"Content-Type: text/plain\r\n", (DWORD)-1L, body, (DWORD)n, (DWORD)n, 0) &&
            WinHttpReceiveResponse(req, NULL)) {
            char reply[LIVE_LINES * LIVE_LINE];
            DWORD got = 0, total = 0;
            while (total < sizeof(reply) - 1 && WinHttpReadData(req, reply + total, (DWORD)(sizeof(reply) - 1 - total), &got) && got)
                total += got;
            reply[total] = 0;
            ok = 1;
            char *next = NULL;
            EnterCriticalSection(&lock);
            for (char *line = strtok_s(reply, "\r\n", &next); line; line = strtok_s(NULL, "\r\n", &next))
                if (live_cmd_count < LIVE_LINES)
                    snprintf(live_cmds[live_cmd_count++], LIVE_LINE, "%s", line);
            LeaveCriticalSection(&lock);
        }
        if (req) WinHttpCloseHandle(req);
        InterlockedExchange(&live_connected, ok);
        Sleep(ok ? 250 : 2000);       /* no editor running: check now and then */
    }
    if (conn) WinHttpCloseHandle(conn);
    WinHttpCloseHandle(session);
    return 0;
}

static void live_result(const char *fmt, ...)
{
    char text[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof(text), fmt, ap);
    va_end(ap);
    EnterCriticalSection(&lock);
    size_t len = strlen(live_results);
    snprintf(live_results + len, sizeof(live_results) - len, "result %s\n", text);
    LeaveCriticalSection(&lock);
    char logged[300];
    snprintf(logged, sizeof(logged), "live: %s", text);
    host->log(host->context, logged);       /* plugin-settings/plugins.log */
}

/* The object a live command means: by name, else the nearest with the model
 * (when given) within 2.5 m of one of the points. */
static uint32_t live_find(const char *name, const char *model, unsigned npts, const float *pts)
{
    MercGuestCall c;
    if (strcmp(name, "-") != 0) {
        uint32_t want = hash_name(name);
        host->read_guest(host->context, NAME_KEYS, name_keys, sizeof(name_keys));
        host->read_guest(host->context, NAME_HANDLES, name_handles, sizeof(name_handles));
        for (unsigned j = 0; j < NAME_SLOTS; j++)
            if (name_keys[j] == want && guest(MF_FN_World_ObjectFromHandle, 0, 1, &name_handles[j], &c) && c.eax)
                return c.eax;
        return 0;
    }
    if (!npts || !host->read_guest(host->context, OBJECTS, slot_objects, sizeof(slot_objects)) ||
        !host->read_guest(host->context, OBJECT_HANDLES, slot_handles, sizeof(slot_handles)))
        return 0;
    uint32_t best = 0;
    float best_d = 2.5f;
    for (unsigned i = 0; i < OBJECT_SLOTS; i++) {
        uint32_t obj = slot_objects[i];
        unsigned char head[0x34];
        if (!obj || !host->read_guest(host->context, obj, head, sizeof(head))) continue;
        uint16_t flags;
        float pos[3];
        memcpy(&flags, head + MF_OFF_WorldObject_flags, 2);
        if (!(flags & 1) || (flags & 4)) continue;
        memcpy(pos, head + WORLDOBJECT_POSITION, 12);
        for (unsigned k = 0; k < npts; k++) {
            float dx = pos[0] - pts[3 * k], dy = pos[1] - pts[3 * k + 1], dz = pos[2] - pts[3 * k + 2];
            float d = sqrtf(dx * dx + dy * dy + dz * dz);
            if (d >= best_d) continue;
            if (strcmp(model, "-") != 0) {
                char m[48];
                uint32_t handle;
                memcpy(&handle, head + MF_OFF_WorldObject_handle, 4);
                object_model(obj, handle, m, sizeof(m));
                if (_stricmp(m, model) != 0) continue;
            }
            best = obj;
            best_d = d;
        }
    }
    return best;
}

/* Place an object (absolute position and heading in radians) or remove it,
 * as do_edit does for the console's buttons. */
static int live_apply(uint32_t obj, const float *pos, float yaw, int remove)
{
    MercGuestCall c;
    uint32_t scratch, size;
    int ok = 0;
    uint16_t flags = 0;
    if (!host->guest_scratch(host->context, &scratch, &size) || size < 256) return 0;
    host->read_guest(host->context, obj + MF_OFF_WorldObject_flags, &flags, 2);
    if (flags & FLAG_PLAIN) {
        if (remove) return guest(MF_FN_WorldObject_Remove, obj, 0, NULL, &c);
        float m[16] = {cosf(yaw), 0, -sinf(yaw), 0, 0, 1, 0, 0, sinf(yaw), 0, cosf(yaw), 0, pos[0], pos[1], pos[2], 1};
        host->write_guest(host->context, scratch, m, sizeof(m));
        vcall(obj, 0x00, 1, &scratch, &ok);
        return ok;
    }
    uint32_t actor = 0, value = 0, mask = 0;
    host->read_guest(host->context, obj + MF_OFF_WorldObject_actor, &actor, 4);
    host->read_guest(host->context, ACTOR_TYPE_VALUE, &value, 4);
    host->read_guest(host->context, ACTOR_TYPE_MASK, &mask, 4);
    uint32_t bits = actor ? vcall(actor, ACTOR_VT_TYPE, 0, NULL, &ok) : 0;
    if (!actor || !ok || (bits & mask) != value) return 0;
    if (remove) { vcall(actor, ACTOR_VT_REMOVE, 0, NULL, &ok); return ok; }
    host->write_guest(host->context, scratch, pos, 12);
    vcall(actor, ACTOR_VT_SET_POSITION, 1, &scratch, &ok);
    if (!ok) return 0;
    uint32_t bitsyaw;
    memcpy(&bitsyaw, &yaw, 4);
    vcall(actor, ACTOR_VT_SET_YAW, 1, &bitsyaw, &ok);
    return ok;
}

/* "loaded": which templates this area has loaded, for the editor's squad
 * picker. The console's own template scan (a few frames) answers it. */
static int live_want_loaded;

static void live_raw(const char *line)
{
    EnterCriticalSection(&lock);
    size_t len = strlen(live_results);
    snprintf(live_results + len, sizeof(live_results) - len, "%s\n", line);
    LeaveCriticalSection(&lock);
}

static void live_report_loaded(void)
{
    if (!live_want_loaded) return;
    live_want_loaded = 0;
    char line[600] = "loaded";
    live_raw("loaded-begin");
    for (unsigned i = 0; i < template_count; i++) {
        if (templates[i].loaded != 1) continue;
        if (strlen(line) + strlen(templates[i].name) + 2 > sizeof(line)) { live_raw(line); strcpy_s(line, sizeof(line), "loaded"); }
        strcat_s(line, sizeof(line), " ");
        strcat_s(line, sizeof(line), templates[i].name);
    }
    if (strcmp(line, "loaded")) live_raw(line);
    live_raw("loaded-end");
}

static float teleport_to[3];
static double teleport_check;        /* seconds until checking a teleport stuck */

static void live_run(char *line)
{
    char *next = NULL;
    char *op = strtok_s(line, " ", &next);
    if (!op) return;
    if (!strcmp(op, "teleport")) {
        float p[3];
        for (int i = 0; i < 3; i++) { char *t = strtok_s(NULL, " ", &next); p[i] = t ? (float)atof(t) : 0; }
        /* As Lua_Actor_SetPosition does it: the named world object's actor
         * (+0x24), type-checked, then its set-position call. If the player
         * isn't there afterwards, a Lua line as well. */
        uint32_t obj = live_find("player0", "-", 0, NULL), scratch, size, actor = 0, value = 0, mask = 0;
        int ok = 0;
        if (obj && host->guest_scratch(host->context, &scratch, &size) && size >= 16) {
            host->read_guest(host->context, obj + MF_OFF_WorldObject_actor, &actor, 4);
            host->read_guest(host->context, ACTOR_TYPE_VALUE, &value, 4);
            host->read_guest(host->context, ACTOR_TYPE_MASK, &mask, 4);
            uint32_t bits = actor ? vcall(actor, ACTOR_VT_TYPE, 0, NULL, &ok) : 0;
            if (actor && ok && (bits & mask) == value) {
                host->write_guest(host->context, scratch, p, 12);
                vcall(actor, ACTOR_VT_SET_POSITION, 1, &scratch, &ok);
            } else {
                ok = 0;
            }
        }
        float m[16];
        uint32_t player = find_player();
        int there = ok && player && host->read_guest(host->context, player + MF_OFF_Actor_transform, m, sizeof(m)) &&
                    fabsf(m[12] - p[0]) < 3.0f && fabsf(m[14] - p[2]) < 3.0f;
        if (!there) {
            char lua[160];
            snprintf(lua, sizeof(lua), "Actor_SetPosition('player0', %.2f, %.2f, %.2f)", p[0], p[1], p[2]);
            run_lua(0, lua);
        }
        live_result(there ? "teleported the player to %.0f, %.0f, %.0f"
                    : ok ? "the player didn't move; asked a script to teleport them to %.0f, %.0f, %.0f"
                         : "asked a script to teleport the player to %.0f, %.0f, %.0f", p[0], p[1], p[2]);
        /* In a vehicle the game puts the player back with it: check shortly. */
        memcpy(teleport_to, p, sizeof(teleport_to));
        teleport_check = 0.5;
        return;
    }
    if (!strcmp(op, "loaded")) {
        live_want_loaded = 1;
        EnterCriticalSection(&lock);
        scan_next = 0;                    /* the console's template scan reports when done */
        LeaveCriticalSection(&lock);
        return;
    }
    if (!strcmp(op, "spawn")) {
        /* spawn <template> <name> <x> <y> <z> <heading>: as Mission_Spawn does. */
        char *tmpl = strtok_s(NULL, " ", &next), *name = strtok_s(NULL, " ", &next);
        float v[4] = {0};
        for (int i = 0; i < 4; i++) { char *t = strtok_s(NULL, " ", &next); v[i] = t ? (float)atof(t) : 0; }
        if (!tmpl || !name) return;
        uint32_t hash = hash_name(tmpl), scratch, size;
        MercGuestCall c;
        if (!template_loaded(hash)) {
            live_result("%s: %s can't be spawned here (not a template loaded in this area); it appears after a rebuild",
                        name, tmpl);
            return;
        }
        if (!host->guest_scratch(host->context, &scratch, &size) || size < 256) return;
        float yaw = v[3] * 3.14159265f / 180.0f;
        float m[16] = {cosf(yaw), 0, -sinf(yaw), 0, 0, 1, 0, 0, sinf(yaw), 0, cosf(yaw), 0, v[0], v[1], v[2], 1};
        host->write_guest(host->context, scratch, m, sizeof(m));
        host->write_guest(host->context, scratch + 64, name, strlen(name) + 1);
        uint32_t args[5] = {hash, scratch + 64, scratch, 0, 0};
        int ok = guest(MF_FN_Spawner_SpawnTemplate, MF_VAR_g_Spawner, 5, args, &c) && c.eax;
        live_result(ok ? "%s spawned (%s)" : "%s: spawning %s failed", name, tmpl);
        return;
    }
    if (!strcmp(op, "inspect")) {
        /* inspect <x> <y> <z> [radius]: every world object that near, one
         * reply each: the slot object, flags, position, actor, property block
         * (and its three words), name hash and GeometryFile. For finding out
         * what a copy or a move found and made. */
        float v[4] = {0, 0, 0, 6};
        for (int i = 0; i < 4; i++) { char *t = strtok_s(NULL, " ", &next); if (t) v[i] = (float)atof(t); }
        if (!host->read_guest(host->context, OBJECTS, slot_objects, sizeof(slot_objects))) return;
        unsigned shown = 0;
        for (unsigned i = 0; i < OBJECT_SLOTS && shown < 12; i++) {
            uint32_t obj = slot_objects[i];
            unsigned char head[0x34];
            if (!obj || !host->read_guest(host->context, obj, head, sizeof(head))) continue;
            float pos[3];
            uint16_t flags;
            uint32_t block, actor, handle, words[3] = {0}, name = 0;
            memcpy(pos, head + WORLDOBJECT_POSITION, 12);
            float dx = pos[0] - v[0], dy = pos[1] - v[1], dz = pos[2] - v[2];
            if (dx * dx + dy * dy + dz * dz > v[3] * v[3]) continue;
            memcpy(&flags, head + MF_OFF_WorldObject_flags, 2);
            memcpy(&block, head + MF_OFF_WorldObject_template, 4);
            memcpy(&actor, head + MF_OFF_WorldObject_actor, 4);
            memcpy(&handle, head + MF_OFF_WorldObject_handle, 4);
            if (block) host->read_guest(host->context, block, words, sizeof(words));
            if (actor) host->read_guest(host->context, actor + 4, &name, 4);
            char m[48];
            object_model(obj, handle, m, sizeof(m));
            live_result("inspect %08x flags %04x at %.1f %.1f %.1f actor %08x name %08x block %08x [%08x %08x %08x] %s",
                        obj, flags, pos[0], pos[1], pos[2], actor, name, block, words[0], words[1], words[2],
                        m[0] ? m : "-");
            shown++;
        }
        if (!shown) live_result("inspect: nothing within %.0f m of %.1f %.1f %.1f", v[3], v[0], v[1], v[2]);
        return;
    }
    int respawn = !strcmp(op, "respawn");
    if (respawn || !strcmp(op, "copy")) {
        /* copy <name> <model> <n> <n points> <new name> <x> <y> <z> <heading>: a
         * copy of an object found as move finds it. Unnamed scenery isn't a
         * template, so its property block is registered as one under a made-up
         * name, then spawned like a template; the copy has the new name.
         * respawn (same arguments): the copy, then the object removed, which is
         * how the editor moves objects (buildings moved by the game stop
         * drawing; a spawned copy draws). The block outlives the removal. */
        char *src = strtok_s(NULL, " ", &next), *model = strtok_s(NULL, " ", &next);
        char *count = strtok_s(NULL, " ", &next);
        if (!src || !model || !count) return;
        unsigned n = (unsigned)atoi(count);
        if (n > 4) n = 4;
        float pts[12], v[4] = {0};
        for (unsigned i = 0; i < 3 * n; i++) { char *t = strtok_s(NULL, " ", &next); pts[i] = t ? (float)atof(t) : 0; }
        char *name = strtok_s(NULL, " ", &next);
        for (int i = 0; i < 4; i++) { char *t = strtok_s(NULL, " ", &next); v[i] = t ? (float)atof(t) : 0; }
        if (!name) return;
        const char *what = strcmp(src, "-") ? src : model;
        uint32_t obj = live_find(src, model, n, pts), block = 0, scratch, size, table_count = 0;
        MercGuestCall c;
        if (!obj) { live_result("%s: not found in the game here, so no copy", what); return; }
        if (!host->read_guest(host->context, obj + MF_OFF_WorldObject_template, &block, 4) || !block) {
            live_result("%s: no properties to copy", what);
            return;
        }
        if (!host->guest_scratch(host->context, &scratch, &size) || size < 256) return;
        char key[40];
        snprintf(key, sizeof(key), "foundry_copy_%08x", block);
        uint32_t hash = hash_name(key);
        uint32_t find[3] = {hash, MF_VAR_g_TemplateTable + 0x4008, 0xFFF};
        if (!guest(MF_FN_HashTable_Find, 0, 3, find, &c)) return;
        if ((int32_t)c.eax >= 0) {
            /* Registered before: the block there may since be another object's. */
            host->write_guest(host->context, MF_VAR_g_TemplateTable + 8 + c.eax * 4, &block, 4);
        } else {
            host->read_guest(host->context, MF_VAR_g_TemplateTable, &table_count, 4);
            if (table_count >= 0xF00) { live_result("%s: the game's template table is full", what); return; }
            host->write_guest(host->context, scratch + 128, &block, 4);
            uint32_t add[2] = {hash, scratch + 128};
            if (!guest(MF_FN_TemplateTable_Add, MF_VAR_g_TemplateTable, 2, add, &c)) return;
        }
        float yaw = v[3] * 3.14159265f / 180.0f;
        float m[16] = {cosf(yaw), 0, -sinf(yaw), 0, 0, 1, 0, 0, sinf(yaw), 0, cosf(yaw), 0, v[0], v[1], v[2], 1};
        host->write_guest(host->context, scratch, m, sizeof(m));
        host->write_guest(host->context, scratch + 64, name, strlen(name) + 1);
        uint32_t args[5] = {hash, scratch + 64, scratch, 0, 0};
        int ok = guest(MF_FN_Spawner_SpawnTemplate, MF_VAR_g_Spawner, 5, args, &c) && c.eax;
        if (respawn && ok) {
            float none[3] = {0, 0, 0};
            int gone = live_apply(obj, none, 0, 1);
            live_result(gone ? "%s moved (as %s)" : "%s: placed as %s, but the old one wouldn't go", what, name);
            return;
        }
        live_result(ok ? "%s: copied as %s" : "%s: copying as %s failed", what, name);
        return;
    }
    int remove = !strcmp(op, "remove");
    if (!remove && strcmp(op, "move")) { live_result("unknown command %s", op); return; }
    char *name = strtok_s(NULL, " ", &next), *model = strtok_s(NULL, " ", &next), *count = strtok_s(NULL, " ", &next);
    if (!name || !model || !count) return;
    unsigned n = (unsigned)atoi(count);
    if (n > 4) n = 4;
    float pts[12], to[4] = {0};
    for (unsigned i = 0; i < 3 * n; i++) { char *t = strtok_s(NULL, " ", &next); pts[i] = t ? (float)atof(t) : 0; }
    for (int i = 0; !remove && i < 4; i++) { char *t = strtok_s(NULL, " ", &next); to[i] = t ? (float)atof(t) : 0; }
    const char *what = strcmp(name, "-") ? name : model;
    uint32_t obj = live_find(name, model, n, pts);
    if (!obj) { live_result("%s: not found in the game here", what); return; }
    int ok = live_apply(obj, to, to[3] * 3.14159265f / 180.0f, remove);
    live_result(ok ? "%s %s" : "%s: the game wouldn't %s it", what,
                ok ? (remove ? "removed" : "moved") : (remove ? "remove" : "move"));
}

/* Game thread: the state for the next poll, then the commands that came. */
static void live_update(double dt)
{
    if (!InterlockedCompareExchange(&live_connected, 0, 0)) return;
    if (teleport_check > 0 && (teleport_check -= dt) <= 0) {
        float m[16];
        uint32_t player = find_player();
        if (player && host->read_guest(host->context, player + MF_OFF_Actor_transform, m, sizeof(m)) &&
            (fabsf(m[12] - teleport_to[0]) > 3.0f || fabsf(m[14] - teleport_to[2]) > 3.0f))
            live_result("the game put the player back; teleport works on foot (get out of the vehicle first)");
    }
    since_live += dt;
    if (since_live < 0.2) return;
    since_live = 0;
    int playing = host->in_gameplay(host->context);
    char state[256] = "";
    uint32_t player = playing ? find_player() : 0;
    float m[16];
    if (player && host->read_guest(host->context, player + MF_OFF_Actor_transform, m, sizeof(m)))
        snprintf(state, sizeof(state), "player %.2f %.2f %.2f %.3f %.3f\ngameplay 1\n", m[12], m[13], m[14], m[8], m[10]);
    else
        snprintf(state, sizeof(state), "gameplay 0\n");
    static char cmds[LIVE_LINES][LIVE_LINE];
    unsigned n;
    EnterCriticalSection(&lock);
    memcpy(live_state, state, sizeof(state));
    n = live_cmd_count;
    memcpy(cmds, live_cmds, n * LIVE_LINE);
    live_cmd_count = 0;
    LeaveCriticalSection(&lock);
    for (unsigned i = 0; i < n; i++) {
        if (playing) live_run(cmds[i]);
        else live_result("not in gameplay; skipped: %.60s", cmds[i]);
    }
}

static void MERC_PLUGIN_CALL on_shutdown(void *ud)
{
    (void)ud;
    InterlockedExchange(&live_stop, 1);
    sampler_shutdown();
    if (window_thread_id) PostThreadMessageW(window_thread_id, WM_QUIT, 0, 0);
}

/* Foundry_TrafficZone('name'): the traffic manager's view of a zone, as a
 * string. The manager is a static object (Traffic_* commands pass 0x3DC970);
 * zones are a table of 0x294-byte entries at +0x2C, count at +0x28 (at most
 * 150): +0 name hash, +4 density, +8 spawner count (at most 15), +0xC the
 * spawners' template names, +0x100 a 12-byte template handle each, +0x290
 * enabled. */
#define TRAFFIC_MANAGER 0x3DC970u
#define TRAFFIC_ZONE_SIZE 0x294u

static int MERC_PLUGIN_CALL cmd_traffic_zone(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    char name[64] = {0}, out[900];
    uint32_t args[2] = {L, 1};
    if (guest(MF_FN_Lua_ArgString, 0, 2, args, &c) && c.eax)
        for (unsigned i = 0; i < sizeof(name) - 1; i++)
            if (!host->read_guest(host->context, c.eax + i, &name[i], 1) || !name[i]) break;
    uint32_t count = 0;
    host->read_guest(host->context, TRAFFIC_MANAGER + 0x28, &count, 4);
    int n = snprintf(out, sizeof(out), "%u zones", count);
    uint32_t want = hash_name(name);
    int found = 0;
    for (uint32_t i = 0; i < count && i < 150; i++) {
        uint32_t zone = TRAFFIC_MANAGER + 0x2C + i * TRAFFIC_ZONE_SIZE, hash = 0;
        host->read_guest(host->context, zone, &hash, 4);
        if (hash != want) continue;
        float density = 0;
        uint32_t spawners = 0;
        uint8_t enabled = 0;
        host->read_guest(host->context, zone + 4, &density, 4);
        host->read_guest(host->context, zone + 8, &spawners, 4);
        host->read_guest(host->context, zone + 0x290, &enabled, 1);
        n += snprintf(out + n, sizeof(out) - n, "; %s is #%u: density %.0f, %s, %u spawner%s", name, i,
                      density, enabled ? "enabled" : "disabled", spawners, spawners == 1 ? "" : "s");
        for (uint32_t s = 0; s < spawners && s < 15 && n < (int)sizeof(out) - 64; s++) {
            uint32_t tmpl = 0, handle[3] = {0};
            host->read_guest(host->context, zone + 0xC + s * 4, &tmpl, 4);
            host->read_guest(host->context, zone + 0x100 + s * 12, handle, 12);
            n += snprintf(out + n, sizeof(out) - n, " [%08X: %08X %08X %08X]", tmpl, handle[0], handle[1], handle[2]);
        }
        /* Routes: 40-byte entries at +0x18B58, count at +0x18B54 (at most
         * 1024): +0 path, +4 zone, +8 vehicle or pedestrian settings, +0x18
         * enabled. Added when a path named t_<zone>_... or p_<zone>_... loads. */
        uint32_t routes = 0, mine = 0, on = 0, people = 0;
        host->read_guest(host->context, TRAFFIC_MANAGER + 0x18B54, &routes, 4);
        for (uint32_t r = 0; r < routes && r < 1024; r++) {
            uint32_t entry = TRAFFIC_MANAGER + 0x18B58 + r * 40, owner = 0, cfg = 0;
            uint8_t en = 0;
            host->read_guest(host->context, entry + 4, &owner, 4);
            host->read_guest(host->context, entry + 8, &cfg, 4);
            host->read_guest(host->context, entry + 0x18, &en, 1);
            if (owner != zone) continue;
            mine++;
            on += en != 0;
            people += cfg == TRAFFIC_MANAGER + 0x22D44;
        }
        n += snprintf(out + n, sizeof(out) - n, "; %u of %u routes (%u enabled, %u for people)",
                      mine, routes, on, people);
        found = 1;
    }
    if (!found)
        snprintf(out + n, sizeof(out) - n, "; no zone named %s", name);
    uint32_t scratch, size;
    if (host->guest_scratch(host->context, &scratch, &size) && size > sizeof(out)) {
        host->write_guest(host->context, scratch, out, strlen(out) + 1);
        uint32_t push[2] = {L, scratch};
        guest(MF_FN_lua_pushstring, 0, 2, push, &c);
        return 1;
    }
    return 0;
}

/* Foundry_Range(): run mods/Foundry Test Range/range.lua in the calling
 * script's Lua state (it loads the range layer, moves the player to the pad
 * and starts the current test). Returns "ok" or the error. */
static void push_text(uint32_t L, const char *text)
{
    MercGuestCall c;
    uint32_t scratch, size;
    if (host->guest_scratch(host->context, &scratch, &size) && size > strlen(text) + 1) {
        host->write_guest(host->context, scratch, text, strlen(text) + 1);
        uint32_t push[2] = {L, scratch};
        guest(MF_FN_lua_pushstring, 0, 2, push, &c);
    }
}

static int MERC_PLUGIN_CALL cmd_range(void *ud, uint32_t L)
{
    (void)ud;
    char path[MAX_PATH], result[512];
    HMODULE self = NULL;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       (LPCSTR)cmd_range, &self);
    /* The launcher runs the game from mod-cache\<id>\runtime, with this DLL in
     * runtime\plugins\foundry-console and each mod's top-level files copied
     * into runtime. Prefer the installation's own copy (edits apply without a
     * relaunch), then the runtime copy, then a plain mods\ layout. */
    char dir[MAX_PATH];
    GetModuleFileNameA(self, dir, sizeof(dir));
    static const char *const tries[] = {
        "\\..\\..\\..\\..\\..\\mods\\Foundry Test Range\\range.lua",
        "\\..\\..\\range.lua",
        "\\..\\Foundry Test Range\\range.lua"};
    char *slash = strrchr(dir, '\\');
    if (slash) *slash = 0;
    FILE *f = NULL;
    for (unsigned t = 0; t < sizeof(tries) / sizeof(tries[0]) && !f; t++) {
        snprintf(path, sizeof(path), "%s%s", dir, tries[t]);
        f = fopen(path, "rb");
    }
    if (!f) { push_text(L, "no mods\\Foundry Test Range\\range.lua"); return 1; }
    static char text[3800];
    size_t n = fread(text, 1, sizeof(text) - 1, f);
    int too_long = !feof(f) && fgetc(f) != EOF;
    fclose(f);
    if (too_long) { push_text(L, "range.lua is too long (3.7 KB at most)"); return 1; }
    uint32_t scratch, size;
    if (!host->guest_scratch(host->context, &scratch, &size) || size < n + 64) {
        push_text(L, "no scratch memory for range.lua");
        return 1;
    }
    const uint32_t name = scratch, body = scratch + 32;
    host->write_guest(host->context, name, "=range", 7);
    host->write_guest(host->context, body, text, n);
    uint32_t args[1] = {L};
    uint32_t top = lua_call(MF_FN_lua_gettop, 1, args);
    uint32_t load[4] = {L, body, (uint32_t)n, name};
    uint32_t status = lua_call(MF_FN_luaL_loadbuffer, 4, load);
    if (!status) {
        uint32_t pcall[4] = {L, 0, 0, 0};
        status = lua_call(MF_FN_lua_pcall, 4, pcall);
    }
    if (status) {
        uint32_t tostr[2] = {L, (uint32_t)-1};
        read_text(lua_call(MF_FN_lua_tostring, 2, tostr), result, sizeof(result));
    } else {
        strcpy(result, "ok");
    }
    uint32_t settop[2] = {L, top};
    lua_call(MF_FN_lua_settop, 2, settop);
    push_text(L, result);
    return 1;
}

/* ---------- passive function sampler ---------- */

/* The port stores the guest address of every function it enters in a host
 * global, g_recomp_current_func. A thread of ours reads it a few thousand
 * times a second and counts hits per (label, address); every FlushSeconds
 * the counts go to <settings>\samples\<session>.tsv as label, address and
 * count lines, for `python -m mercfoundry funcmap profile`. The game thread
 * only caches in_gameplay for it (once a frame) and sets the label.
 *
 * Where the variable is comes from sampler-address.ini, written by
 * `mercfoundry mod sync` from the exe's linker map. It is used only when the
 * running exe's PE timestamp equals the map's and the address lies in a
 * writable data section of the exe; otherwise sampling stays off. */
#define SAMPLE_SLOTS (1u << 16)
#define SAMPLE_LABELS 32
#define SAMPLE_LABEL_LEN 48
#define SAMPLE_BURST_MAX 256
typedef struct SampleSlot { uint32_t address, count; uint16_t label, used; } SampleSlot;
static SampleSlot *sample_table;
static unsigned sample_used;
static char sample_labels[SAMPLE_LABELS][SAMPLE_LABEL_LEN];
static unsigned sample_label_count;
static const volatile uint32_t *sample_var;     /* NULL: sampling unavailable */
static char sample_off_reason[200] = "sampler not set up";
static volatile LONG sample_on, sample_stop, sample_playing;
static volatile LONG sample_rate = 8000, sample_flush = 10, sample_achieved;
static CRITICAL_SECTION sample_lock;            /* manual_label, sample_file */
static char manual_label[SAMPLE_LABEL_LEN];
static wchar_t sample_file[MAX_PATH * 2];
static char sample_file_utf8[MAX_PATH * 3];
static HANDLE sample_thread;

static int widen(const char *utf8, wchar_t *out, int size)
{
    return MultiByteToWideChar(CP_UTF8, 0, utf8, -1, out, size) > 0;
}

/* Find g_recomp_current_func from the hand-off file, checked against this exe. */
static void sampler_resolve(void)
{
    wchar_t path[MAX_PATH * 2], dir[MAX_PATH * 2], buf[64];
    if (!host->settings_directory || !widen(host->settings_directory, dir, MAX_PATH * 2)) {
        snprintf(sample_off_reason, sizeof(sample_off_reason), "no plugin settings folder");
        return;
    }
    _snwprintf_s(path, MAX_PATH * 2, _TRUNCATE, L"%s\\sampler-address.ini", dir);
    if (GetFileAttributesW(path) == INVALID_FILE_ATTRIBUTES) {
        snprintf(sample_off_reason, sizeof(sample_off_reason),
                 "no sampler-address.ini (run python -m mercfoundry mod sync or funcmap sampler-setup)");
        return;
    }
    GetPrivateProfileStringW(L"CurrentFunc", L"Symbol", L"", buf, 64, path);
    if (wcscmp(buf, L"g_recomp_current_func")) {
        snprintf(sample_off_reason, sizeof(sample_off_reason), "sampler-address.ini names no g_recomp_current_func");
        return;
    }
    GetPrivateProfileStringW(L"CurrentFunc", L"Rva", L"", buf, 64, path);
    unsigned long long rva = wcstoull(buf, NULL, 16);
    GetPrivateProfileStringW(L"CurrentFunc", L"Timestamp", L"", buf, 64, path);
    unsigned long long stamp = buf[0] ? wcstoull(buf, NULL, 16) : 0x100000000ull;
    const BYTE *image = (const BYTE *)GetModuleHandleW(NULL);
    const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)image;
    if (!image || dos->e_magic != IMAGE_DOS_SIGNATURE) {
        snprintf(sample_off_reason, sizeof(sample_off_reason), "can't read this exe's header");
        return;
    }
    const IMAGE_NT_HEADERS64 *nt = (const IMAGE_NT_HEADERS64 *)(image + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE || nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC) {
        snprintf(sample_off_reason, sizeof(sample_off_reason), "can't read this exe's header");
        return;
    }
    if (stamp != nt->FileHeader.TimeDateStamp) {
        snprintf(sample_off_reason, sizeof(sample_off_reason),
                 "sampling off: sampler-address.ini is for exe 0x%08llX but this exe is 0x%08lX "
                 "(the port was rebuilt; run mod sync again)", stamp, (unsigned long)nt->FileHeader.TimeDateStamp);
        return;
    }
    /* The variable must be in a writable, non-code section of this image. */
    const IMAGE_SECTION_HEADER *sec = IMAGE_FIRST_SECTION(nt);
    int ok = 0;
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; i++, sec++) {
        DWORD size = sec->Misc.VirtualSize > sec->SizeOfRawData ? sec->Misc.VirtualSize : sec->SizeOfRawData;
        if (rva >= sec->VirtualAddress && rva + 4 <= (unsigned long long)sec->VirtualAddress + size)
            ok = (sec->Characteristics & IMAGE_SCN_MEM_WRITE) && !(sec->Characteristics & IMAGE_SCN_MEM_EXECUTE) &&
                 (rva & 3) == 0;
    }
    if (!ok || rva + 4 > nt->OptionalHeader.SizeOfImage) {
        snprintf(sample_off_reason, sizeof(sample_off_reason),
                 "sampling off: RVA 0x%llX isn't in a data section of this exe", rva);
        return;
    }
    sample_var = (const volatile uint32_t *)(image + rva);
    sample_off_reason[0] = 0;
}

static unsigned sample_label_index(const char *name)
{
    for (unsigned i = 0; i < sample_label_count; i++)
        if (!strcmp(sample_labels[i], name)) return i;
    if (sample_label_count < SAMPLE_LABELS - 1) {
        snprintf(sample_labels[sample_label_count], SAMPLE_LABEL_LEN, "%s", name);
        return sample_label_count++;
    }
    snprintf(sample_labels[SAMPLE_LABELS - 1], SAMPLE_LABEL_LEN, "other");   /* too many labels */
    return SAMPLE_LABELS - 1;
}

/* The automatic label: "menu" outside gameplay; in gameplay the manual label
 * (Foundry_Label) or "gameplay". */
static unsigned sample_current_label(void)
{
    char name[SAMPLE_LABEL_LEN];
    if (!InterlockedCompareExchange(&sample_playing, 0, 0)) return sample_label_index("menu");
    EnterCriticalSection(&sample_lock);
    snprintf(name, sizeof(name), "%s", manual_label[0] ? manual_label : "gameplay");
    LeaveCriticalSection(&sample_lock);
    return sample_label_index(name);
}

static void sample_flush_now(double seconds, unsigned long long samples, unsigned long long wakes)
{
    if (!sample_used) return;
    wchar_t path[MAX_PATH * 2];
    EnterCriticalSection(&sample_lock);
    memcpy(path, sample_file, sizeof(path));
    LeaveCriticalSection(&sample_lock);
    FILE *f = path[0] ? _wfopen(path, L"ab") : NULL;
    if (f) {
        fseek(f, 0, SEEK_END);
        if (ftell(f) == 0)
            fprintf(f, "# Foundry Console function samples (python -m mercfoundry funcmap profile)\n"
                       "label\taddress\tcount\n");
        fprintf(f, "# %.0f s sampled, %llu samples (%.0f/s), %llu wakes (%.0f/s)\n", seconds, samples,
                seconds > 0 ? samples / seconds : 0.0, wakes, seconds > 0 ? wakes / seconds : 0.0);
        for (unsigned i = 0; i < SAMPLE_SLOTS; i++)
            if (sample_table[i].used)
                fprintf(f, "%s\t0x%08X\t%u\n", sample_labels[sample_table[i].label], sample_table[i].address,
                        sample_table[i].count);
        fclose(f);
    }
    memset(sample_table, 0, SAMPLE_SLOTS * sizeof(SampleSlot));
    sample_used = 0;
}

static void sample_count(uint32_t address, unsigned label)
{
    unsigned h = ((address >> 2) * 2654435761u ^ label * 0x9E3779B9u) & (SAMPLE_SLOTS - 1);
    for (;;) {
        SampleSlot *s = &sample_table[h];
        if (!s->used) {
            s->used = 1;
            s->address = address;
            s->label = (uint16_t)label;
            s->count = 1;
            sample_used++;
            return;
        }
        if (s->address == address && s->label == label) { s->count++; return; }
        h = (h + 1) & (SAMPLE_SLOTS - 1);
    }
}

#ifndef CREATE_WAITABLE_TIMER_HIGH_RESOLUTION
#define CREATE_WAITABLE_TIMER_HIGH_RESOLUTION 0x00000002
#endif

static DWORD WINAPI sampler_thread(void *arg)
{
    (void)arg;
    HANDLE timer = CreateWaitableTimerExW(NULL, NULL, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    if (!timer) timer = CreateWaitableTimerExW(NULL, NULL, 0, TIMER_ALL_ACCESS);
    LARGE_INTEGER freq, now, last, flushed, spin;
    QueryPerformanceFrequency(&freq);
    const LONGLONG spacing = freq.QuadPart / 500000;      /* 2 us between reads of a burst */
    double owed = 0;
    int was_on = 0;
    unsigned long long samples = 0, wakes = 0;
    int logged = 0;
    QueryPerformanceCounter(&last);
    flushed = last;
    while (!InterlockedCompareExchange(&sample_stop, 0, 0)) {
        int on = InterlockedCompareExchange(&sample_on, 0, 0) != 0;
        QueryPerformanceCounter(&now);
        double since_flush = (double)(now.QuadPart - flushed.QuadPart) / freq.QuadPart;
        if (was_on && (!on || since_flush >= InterlockedCompareExchange(&sample_flush, 0, 0) ||
                       sample_used > SAMPLE_SLOTS * 3 / 4)) {
            sample_flush_now(since_flush, samples, wakes);
            InterlockedExchange(&sample_achieved, since_flush > 0 ? (LONG)(samples / since_flush) : 0);
            if (!logged && samples) {
                char text[200];
                snprintf(text, sizeof(text), "sampler: %.0f reads/s (%.0f wakes/s) of %ld asked",
                         samples / since_flush, wakes / since_flush, (long)sample_rate);
                host->log(host->context, text);
                logged = 1;
            }
            samples = wakes = 0;
            flushed = now;
        }
        if (!on) {
            was_on = 0;
            Sleep(100);
            continue;
        }
        if (!was_on) { last = flushed = now; owed = 0; was_on = 1; }
        LONG rate = InterlockedCompareExchange(&sample_rate, 0, 0);
        if (timer) {
            LARGE_INTEGER due;
            due.QuadPart = -(LONGLONG)(10000000 / rate);     /* relative, 100 ns units */
            if (SetWaitableTimer(timer, &due, 0, NULL, NULL, FALSE)) WaitForSingleObject(timer, 50);
            else Sleep(1);
        } else {
            Sleep(1);
        }
        QueryPerformanceCounter(&now);
        owed += (double)(now.QuadPart - last.QuadPart) * rate / freq.QuadPart;
        last = now;
        unsigned n = owed >= SAMPLE_BURST_MAX ? SAMPLE_BURST_MAX : (unsigned)owed;
        owed = owed >= SAMPLE_BURST_MAX ? 0 : owed - n;
        if (!n) continue;
        unsigned label = sample_current_label();
        wakes++;
        for (unsigned i = 0; i < n; i++) {
            sample_count(*sample_var, label);
            if (i + 1 < n) {
                QueryPerformanceCounter(&spin);
                LONGLONG until = spin.QuadPart + spacing;
                do { YieldProcessor(); QueryPerformanceCounter(&spin); } while (spin.QuadPart < until);
            }
        }
        samples += n;
    }
    QueryPerformanceCounter(&now);
    if (was_on) sample_flush_now((double)(now.QuadPart - flushed.QuadPart) / freq.QuadPart, samples, wakes);
    if (timer) CloseHandle(timer);
    return 0;
}

/* Turn sampling on or off. Returns a line saying what happened. */
static void sampler_set(int on, char *out, size_t size)
{
    if (!sample_var) { snprintf(out, size, "%s", sample_off_reason); return; }
    if (on && !sample_file[0]) {
        wchar_t dir[MAX_PATH * 2], path[MAX_PATH * 2];
        SYSTEMTIME t;
        GetLocalTime(&t);
        widen(host->settings_directory, dir, MAX_PATH * 2);
        _snwprintf_s(path, MAX_PATH * 2, _TRUNCATE, L"%s\\samples", dir);
        CreateDirectoryW(path, NULL);
        EnterCriticalSection(&sample_lock);
        _snwprintf_s(sample_file, MAX_PATH * 2, _TRUNCATE, L"%s\\%04u%02u%02u-%02u%02u%02u.tsv", path, t.wYear,
                     t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
        snprintf(sample_file_utf8, sizeof(sample_file_utf8), "samples\\%04u%02u%02u-%02u%02u%02u.tsv", t.wYear,
                 t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
        LeaveCriticalSection(&sample_lock);
    }
    if (on && !sample_thread) {
        sample_table = (SampleSlot *)calloc(SAMPLE_SLOTS, sizeof(SampleSlot));
        if (sample_table) sample_thread = CreateThread(NULL, 0, sampler_thread, NULL, 0, NULL);
        if (!sample_thread) { snprintf(out, size, "couldn't start the sampler thread"); return; }
        SetThreadPriority(sample_thread, THREAD_PRIORITY_ABOVE_NORMAL);
    }
    InterlockedExchange(&sample_on, on);
    if (on) snprintf(out, size, "sampling on: %ld/s into plugin-settings\\foundry-console\\%s", (long)sample_rate,
                     sample_file_utf8);
    else snprintf(out, size, "sampling off");
    host->log(host->context, out);
}

/* "Sampler: on, label tank, 7,950 reads/s" for the inspector. */
static int sampler_status(char *out, size_t size)
{
    char label[SAMPLE_LABEL_LEN];
    EnterCriticalSection(&sample_lock);
    snprintf(label, sizeof(label), "%s", manual_label[0] ? manual_label : "auto");
    LeaveCriticalSection(&sample_lock);
    if (!sample_var) return snprintf(out, size, "Sampler: unavailable\r\n");
    if (!InterlockedCompareExchange(&sample_on, 0, 0)) return snprintf(out, size, "Sampler: off (label %s)\r\n", label);
    return snprintf(out, size, "Sampler: on, label %s, %ld reads/s\r\n", label,
                    (long)InterlockedCompareExchange(&sample_achieved, 0, 0));
}

static int lua_arg_text(uint32_t L, int n, char *out, size_t size)
{
    uint32_t args[2] = {L, (uint32_t)n};
    out[0] = 0;
    if ((int)lua_call(MF_FN_lua_gettop, 1, args) < n || !lua_call(MF_FN_lua_isstring, 2, args)) return 0;
    read_text(lua_call(MF_FN_lua_tostring, 2, args), out, size);
    return 1;
}

/* Foundry_Sample(on): 1/true/'on' starts sampling, 0/false/'off' stops it;
 * no argument just reports. Returns a status line. */
static int MERC_PLUGIN_CALL cmd_sample(void *ud, uint32_t L)
{
    (void)ud;
    char text[64], out[400];
    uint32_t args[2] = {L, 1};
    int on;
    if ((int)lua_call(MF_FN_lua_gettop, 1, args) < 1) {
        sampler_status(out, sizeof(out));
        out[strcspn(out, "\r\n")] = 0;
        if (!sample_var) snprintf(out, sizeof(out), "%s", sample_off_reason);
        push_text(L, out);
        return 1;
    }
    if (lua_arg_text(L, 1, text, sizeof(text)))
        on = !(!text[0] || !strcmp(text, "0") || !_stricmp(text, "off") || !_stricmp(text, "false") ||
               !_stricmp(text, "no"));
    else
        on = lua_call(MF_FN_Lua_ArgBool, 2, args) != 0;
    sampler_set(on, out, sizeof(out));
    push_text(L, out);
    return 1;
}

/* Foundry_Label('tank'): the label for samples while in gameplay; '' or no
 * argument goes back to the automatic "gameplay". */
static int MERC_PLUGIN_CALL cmd_label(void *ud, uint32_t L)
{
    (void)ud;
    char text[SAMPLE_LABEL_LEN], out[96];
    lua_arg_text(L, 1, text, sizeof(text));
    for (char *p = text; *p; p++)
        if ((unsigned char)*p < 33 || *p == '#') *p = '_';      /* one TSV field */
    EnterCriticalSection(&sample_lock);
    snprintf(manual_label, sizeof(manual_label), "%s", text);
    LeaveCriticalSection(&sample_lock);
    snprintf(out, sizeof(out), text[0] ? "label %s" : "label automatic (gameplay / menu)", text);
    host->log(host->context, out);
    push_text(L, out);
    return 1;
}

static void sampler_init(void)
{
    wchar_t dir[MAX_PATH * 2], path[MAX_PATH * 2];
    InitializeCriticalSection(&sample_lock);
    sampler_resolve();
    int enabled = 0;
    if (host->settings_directory && widen(host->settings_directory, dir, MAX_PATH * 2)) {
        _snwprintf_s(path, MAX_PATH * 2, _TRUNCATE, L"%s\\foundry-console.ini", dir);
        enabled = GetPrivateProfileIntW(L"Sampler", L"Enabled", 0, path) != 0;
        int rate = (int)GetPrivateProfileIntW(L"Sampler", L"Rate", 8000, path);
        int flush = (int)GetPrivateProfileIntW(L"Sampler", L"FlushSeconds", 10, path);
        sample_rate = rate < 100 ? 100 : rate > 20000 ? 20000 : rate;
        sample_flush = flush < 1 ? 1 : flush > 3600 ? 3600 : flush;
    }
    if (!sample_var) {
        char text[260];
        snprintf(text, sizeof(text), "function sampler unavailable: %s", sample_off_reason);
        host->log(host->context, text);
        return;
    }
    if (enabled) {
        char out[400];
        sampler_set(1, out, sizeof(out));
    }
}

static void sampler_shutdown(void)
{
    if (!sample_thread) return;
    InterlockedExchange(&sample_stop, 1);
    WaitForSingleObject(sample_thread, 2000);   /* writes the last counts */
    CloseHandle(sample_thread);
    sample_thread = NULL;
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
    load_names();
    if (!h->hook_guest(h->context, MF_FN_Spawner_SpawnTemplate, NULL, spawn_after, NULL))
        h->log(h->context, "spawn hook unavailable (run port-hooks); recent spawns won't show");
    if (!h->hook_guest(h->context, MF_FN_LuaScript_RegisterCommands, NULL, script_loaded, NULL))
        h->log(h->context, "script hook unavailable (run port-hooks); the Lua console can't find scripts");
    if (!MERC_PLUGIN_HOST_HAS(h, add_lua_command) ||
        !h->add_lua_command(h->context, "Foundry_TrafficZone", cmd_traffic_zone, NULL) ||
        !h->add_lua_command(h->context, "Foundry_Range", cmd_range, NULL) ||
        !h->add_lua_command(h->context, "Foundry_Sample", cmd_sample, NULL) ||
        !h->add_lua_command(h->context, "Foundry_Label", cmd_label, NULL))
        h->log(h->context, "script commands unavailable; Foundry_TrafficZone, Foundry_Range, Foundry_Sample "
                           "and Foundry_Label may not exist");
    sampler_init();
    plugin->on_update = on_update;
    plugin->on_shutdown = on_shutdown;
    HANDLE t = CreateThread(NULL, 0, window_thread, NULL, 0, &window_thread_id);
    if (t) CloseHandle(t);
    t = CreateThread(NULL, 0, live_thread, NULL, 0, NULL);
    if (t) CloseHandle(t);
    char text[96];
    snprintf(text, sizeof(text), "ready: %u templates; press F7 in game", template_count);
    h->log(h->context, text);
    return 1;
}
