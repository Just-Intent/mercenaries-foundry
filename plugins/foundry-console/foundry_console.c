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
#include <stdlib.h>
#include <math.h>
#include <ctype.h>
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
static void maptex_poll(double dt);
static void maptext_poll(double dt);
static void hq_dump_poll(double dt);
static void ground_probe_poll(double dt);
static void maptemplate_poll(double dt);
static void any_captive_poll(double dt);
static void shop_poll(double dt);
static void shop_level_reset(void);
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

/* The game's world border (World_CreateBorderWalls): four walls and a ceiling,
 * Havok static boxes sized for retail's 512-cell, 8 m terrain (+-2,048 m).
 * On a bigger map they stand inside it (a 1,024-cell map's player stopped at
 * x 2,112). Make the same five boxes moved out by the extra half-width, so
 * they keep retail's place relative to the terrain's edge; retail-sized maps
 * keep the game's own. */
static void MERC_PLUGIN_CALL border_before(void *ud, MercHookContext *c)
{
    (void)ud;
    int32_t size = 0;
    float spacing = 0;
    uint32_t scratch = 0, bytes = 0;
    host->read_guest(host->context, TERRAIN_SIZE, &size, 4);
    host->read_guest(host->context, TERRAIN_SPACING, &spacing, 4);
    float d = size * spacing / 2.0f - 2048.0f;
    if (size <= 0 || spacing <= 0 || d <= 0.5f) return;
    if (!host->guest_scratch(host->context, &scratch, &bytes) || bytes < 32) return;
    const struct { float half[3], centre[3]; uint32_t store; } box[5] = {
        {{2112 + d, 32, 2112 + d}, {0, 1056, 0}, MF_VAR_g_WorldBorderBodies},            /* ceiling */
        {{32, 1088, 2112 + d}, {2080 + d, 0, 0}, MF_VAR_g_WorldBorderBodies + 4},         /* x + */
        {{32, 1088, 2112 + d}, {-2080 - d, 0, 0}, MF_VAR_g_WorldBorderBodies + 8},        /* x - */
        {{2112 + d, 1088, 32}, {0, 0, -2072 - d}, MF_VAR_g_WorldBorderBodies + 12},       /* z - */
        {{2112 + d, 1088, 32}, {0, 0, 2088 + d}, MF_VAR_g_WorldBorderBodies + 16},        /* z + */
    };
    for (int i = 0; i < 5; i++) {
        MercGuestCall call;
        uint32_t args[2] = {scratch, scratch + 16};
        host->write_guest(host->context, scratch, box[i].half, 12);
        host->write_guest(host->context, scratch + 16, box[i].centre, 12);
        uint32_t body = guest(MF_FN_Physics_AddStaticBox, 0, 2, args, &call) ? call.eax : 0;
        host->write_guest(host->context, box[i].store, &body, 4);
    }
    c->skip = 1;
    c->eax = 0;
    c->pop_bytes = 0;
    char msg[128];
    snprintf(msg, sizeof(msg), "world border fitted to the %d-cell terrain (+-%.0f m, retail's +-2048)", size,
             2048.0f + d);
    host->log(host->context, msg);
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

/* Run a line of Lua in a loaded script's state (serial 0: the newest), as an
 * expression when it is one. result gets its values, the error, or why it
 * couldn't run. Returns 0 if it ran, 1 if Lua raised an error, -1 if it
 * couldn't run. *expression: whether it ran as an expression. */
static int lua_line(int serial, const char *line, char *result, size_t rsize, int *expression)
{
    Script target = {0};
    *expression = 0;
    result[0] = 0;
    EnterCriticalSection(&lock);
    for (unsigned i = 0; i < script_count; i++)
        if ((serial == 0 && scripts[i].serial > target.serial) || (serial > 0 && scripts[i].serial == (unsigned)serial))
            target = scripts[i];
    LeaveCriticalSection(&lock);
    if (!target.L) { snprintf(result, rsize, "no script loaded yet"); return -1; }
    /* Only use a state the game still knows. */
    uint32_t args[4];
    args[0] = target.L;
    if (lua_call(MF_FN_LuaScript_FromState, 1, args) != target.object) {
        snprintf(result, rsize, "script #%u has been unloaded; pick another", target.serial);
        return -1;
    }
    uint32_t scratch, size;
    if (!host->guest_scratch(host->context, &scratch, &size) || size < 4096) { snprintf(result, rsize, "no scratch memory"); return -1; }
    static const char prefix[] =
        "local function __out(...) local s = '' for i = 1, arg.n do if i > 1 then s = s .. '   ' end "
        "s = s .. tostring(arg[i]) end return s end return __out(";
    char chunk[1400];
    const uint32_t name = scratch, text = scratch + 32;
    host->write_guest(host->context, name, "=console", 9);
    uint32_t top = lua_call(MF_FN_lua_gettop, 1, args);
    /* Try it as an expression (show its values), then as a statement. */
    *expression = 1;
    int len = snprintf(chunk, sizeof(chunk), "%s%s\n)", prefix, line);
    host->write_guest(host->context, text, chunk, (size_t)len + 1);
    uint32_t load[4] = {target.L, text, (uint32_t)len, name};
    uint32_t status = lua_call(MF_FN_luaL_loadbuffer, 4, load);
    if (status) {
        uint32_t settop[2] = {target.L, top};
        lua_call(MF_FN_lua_settop, 2, settop);
        *expression = 0;
        len = snprintf(chunk, sizeof(chunk), "%s", line);
        host->write_guest(host->context, text, chunk, (size_t)len + 1);
        load[2] = (uint32_t)len;
        status = lua_call(MF_FN_luaL_loadbuffer, 4, load);
    }
    if (!status) {
        uint32_t pcall[4] = {target.L, 0, 1, 0};
        status = lua_call(MF_FN_lua_pcall, 4, pcall);
    }
    uint32_t tostr[2] = {target.L, (uint32_t)-1};
    read_text(lua_call(MF_FN_lua_tostring, 2, tostr), result, rsize);
    uint32_t settop[2] = {target.L, top};
    lua_call(MF_FN_lua_settop, 2, settop);
    if (status && !result[0]) snprintf(result, rsize, "(no message)");
    return status ? 1 : 0;
}

static void run_lua(int serial, const char *line)
{
    char result[1024];
    int expression = 0;
    int status = lua_line(serial, line, result, sizeof(result), &expression);
    if (status < 0) append_output("> %s\r\n  %s", line, result);
    else if (status) append_output("> %s\r\n  error: %s", line, result);
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

/* Slash commands typed in the panel (instead of Lua): what the window's
 * Spawn and Nearby tabs do, for when the window can't show (fullscreen).
 * They run here, on the game thread, through the window's own do_spawn and
 * do_edit; their status goes to the panel's output. */
static int contains(const char *hay, const char *needle);
static uint32_t hud_pick;           /* the object /pick chose (a handle) */
static char hud_pick_label[64];

static void hud_status(void)
{
    EnterCriticalSection(&lock);
    char line[256];
    snprintf(line, sizeof(line), "%s", status_text);
    LeaveCriticalSection(&lock);
    append_output("%s", line);
}

/* The game's objectives (retail Objective_FindOrCreate, 0x15FEC0, and its
 * pool scan 0x15FE60): 16 objectives of 0x1760 bytes from 0x3C31B8, in use
 * when the byte at +0x154 is set, named by the hash at +0x158; the count at
 * 0x3C30B0. A full pool makes Objective_* calls do nothing. Each has 32
 * targets of 0xB0 bytes from +0x160 (Objective_SetTargetVector, 0x160520):
 * the target's type at +4, its GPS / PDA / world bits at +8, its position
 * at +0x94 and its name hash at +0xA0. */
#define OBJECTIVE_POOL 0x003C31B8u
#define OBJECTIVE_SIZE 0x1760u
#define OBJECTIVE_SLOTS 16u
#define OBJECTIVE_COUNT 0x003C30B0u
#define OBJECTIVE_TARGETS 0x160u
#define TARGET_SIZE 0xB0u

static const char *objective_name(uint32_t hash, char *buf, size_t n)
{
    static const char *mine[] = {"foundry_hq_allies", "foundry_hq_china", "foundry_hq_mafia", "foundry_hq_sk",
                                 "foundry_hq_nk"};
    const char *text = name_for_hash(hash);
    if (text) return text;
    for (unsigned i = 0; i < sizeof(mine) / sizeof(mine[0]); i++)
        if (hash_name(mine[i]) == hash) return mine[i];
    snprintf(buf, n, "#%08X", hash);
    return buf;
}

static const char *target_type_name(uint32_t t)
{
    static const char *types[] = {"primary", "secondary", "?2", "?3", "?4", "hq_sk", "hq_nk", "hq_allies",
                                  "hq_mafia", "hq_china", "sams", "?11", "?12", "undefined"};
    return t < sizeof(types) / sizeof(types[0]) ? types[t] : "?";
}

static void hud_objectives(void)
{
    uint32_t count = 0;
    host->read_guest(host->context, OBJECTIVE_COUNT, &count, sizeof(count));
    append_output("%u objectives (the game holds %u)", count, OBJECTIVE_SLOTS);
    for (unsigned i = 0; i < OBJECTIVE_SLOTS; i++) {
        uint32_t base = OBJECTIVE_POOL + i * OBJECTIVE_SIZE;
        unsigned char head[8];
        if (!host->read_guest(host->context, base + 0x154, head, sizeof(head)) || !head[0]) continue;
        uint32_t hash;
        memcpy(&hash, head + 4, 4);
        char nb[16], tb[16];
        const char *name = objective_name(hash, nb, sizeof(nb));
        unsigned shown = 0;
        for (unsigned t = 0; t < 32; t++) {
            unsigned char tg[TARGET_SIZE];
            if (!host->read_guest(host->context, base + OBJECTIVE_TARGETS + t * TARGET_SIZE, tg, sizeof(tg))) break;
            uint32_t thash, type;
            float pos[3];
            memcpy(&thash, tg + 0xA0, 4);
            if (!thash) continue;
            memcpy(&type, tg + 4, 4);
            memcpy(pos, tg + 0x94, 12);
            append_output("%s: %s %s, bits %u, at %.0f %.0f %.0f", name, objective_name(thash, tb, sizeof(tb)),
                          target_type_name(type), (unsigned)(tg[8] & 7), (double)pos[0], (double)pos[1], (double)pos[2]);
            shown++;
        }
        if (!shown) append_output("%s: no targets", name);
    }
}

static void hud_command(const char *line)
{
    char cmd[16] = {0}, arg[96] = {0};
    sscanf_s(line, "%15s %95[^\n]", cmd, (unsigned)sizeof(cmd), arg, (unsigned)sizeof(arg));
    for (char *p = cmd; *p; p++) *p = (char)tolower((unsigned char)*p);
    append_output("/%s", line);
    if (!strcmp(cmd, "spawn")) {
        if (!arg[0]) { append_output("/spawn <template, or part of its name>"); return; }
        int exact = -1, first = -1, count = 0;
        char list[200] = {0};
        for (unsigned i = 0; i < template_count; i++) {
            if (!_stricmp(templates[i].name, arg)) { exact = (int)i; break; }
            if (contains(templates[i].name, arg)) {
                if (first < 0) first = (int)i;
                if (count < 5) {
                    size_t n = strlen(list);
                    snprintf(list + n, sizeof(list) - n, "%s%s", n ? ", " : "", templates[i].name);
                }
                count++;
            }
        }
        int pick = exact >= 0 ? exact : count == 1 ? first : -1;
        if (pick < 0) {
            if (count)
                append_output("%d templates match: %s%s (type more of the name)", count, list, count > 5 ? ", ..." : "");
            else
                append_output("no template matches %s", arg);
            return;
        }
        do_spawn(pick);
        hud_status();
    } else if (!strcmp(cmd, "near")) {
        scan_nearby();
        EnterCriticalSection(&lock);
        unsigned n = nearby_count < 8 ? nearby_count : 8;
        Nearby list[8];
        memcpy(list, nearby, n * sizeof(Nearby));
        LeaveCriticalSection(&lock);
        if (!n) append_output("nothing within %.0f m", (double)NEARBY_RADIUS);
        for (unsigned i = 0; i < n; i++)
            append_output("%u  %s%s%s  %.0f m", i + 1, list[i].name[0] ? list[i].name : "[",
                          list[i].name[0] ? "" : list[i].model, list[i].name[0] ? "" : "]", (double)list[i].distance);
    } else if (!strcmp(cmd, "probe")) {
        /* /probe: every object within 40 m written to the log (the console's
         * output isn't), with the properties /pick shows: for telling what
         * a thing in the world is from a playtest's logs. */
        scan_nearby();
        static Nearby list[MAX_NEARBY];
        EnterCriticalSection(&lock);
        unsigned n = nearby_count;
        memcpy(list, nearby, n * sizeof(Nearby));
        LeaveCriticalSection(&lock);
        static const char *props[] = {"objectType", "faction", "hitPoints", "GeometryFile", "squad", "path"};
        unsigned logged = 0;
        for (unsigned i = 0; i < n; i++) {
            if (list[i].distance > 40.0f) break;
            char line[1100];
            uint16_t flags = 0;
            host->read_guest(host->context, list[i].object + MF_OFF_WorldObject_flags, &flags, 2);
            int k = snprintf(line, sizeof(line), "probe: %s%s%s at %.1f, %.1f, %.1f (%.0f m) handle 0x%08X flags 0x%04X",
                             list[i].name[0] ? list[i].name : "[", list[i].name[0] ? "" : list[i].model,
                             list[i].name[0] ? "" : "]", list[i].pos[0], list[i].pos[1], list[i].pos[2],
                             (double)list[i].distance, list[i].handle, flags);
            uint32_t vtable = 0, getter = 0;
            host->read_guest(host->context, list[i].object, &vtable, 4);
            if (vtable) host->read_guest(host->context, vtable + 0x0C, &getter, 4);
            for (unsigned p = 0; getter && p < sizeof(props) / sizeof(props[0]) && k < (int)sizeof(line) - 1; p++) {
                MercGuestCall c;
                uint32_t hash = hash_name(props[p]);
                char value[96];
                if (!guest(getter, list[i].object, 1, &hash, &c) || !c.eax) continue;
                read_text(c.eax, value, sizeof(value));
                if (value[0]) k += snprintf(line + k, sizeof(line) - k, " %s=%s", props[p], value);
            }
            /* 0.340: for an actor, what decides whether it can be hurt and how it
             * answers: the invincible bit Actor_IsInvincible reads (actor +0x124
             * bit 0, 0x115E50), its AI (vtable +0xBC, as Lua_ArgAi finds it) and
             * how many object slots point at the same actor (0.339's probe of
             * Gaza's unshootable Grove soldiers showed nothing else apart) */
            uint32_t actor = 0;
            if (host->read_guest(host->context, list[i].object + MF_OFF_WorldObject_actor, &actor, 4) && actor &&
                k < (int)sizeof(line) - 80) {
                uint8_t inv = 0;
                int okai = 0;
                host->read_guest(host->context, actor + 0x124u, &inv, 1);
                const uint32_t ai = vcall(actor, 0xBC, 0, NULL, &okai);
                unsigned same = 0;
                for (unsigned j = 0; j < n; j++) {
                    uint32_t aj = 0;
                    if (host->read_guest(host->context, list[j].object + MF_OFF_WorldObject_actor, &aj, 4) && aj == actor) same++;
                }
                k += snprintf(line + k, sizeof(line) - k, " actor=%08X invincible=%u ai=%08X objects=%u",
                              actor, inv & 1u, okai ? ai : 0u, same);
                /* 0.342: a human's bullet hitbox, from the code (not seen in game):
                 * one Havok body at actor +0x76C (Human_SetCollision 0x52130),
                 * its shape one of four global ones chosen when it's built
                 * (0x13C700): 37A320 the standing capsule, 37A380 a flat 20 cm
                 * slab (built while the stance, actor +0x288, wasn't 0), 37A350
                 * the long capsule (dead or a state), 37A2F0 unused; it follows
                 * the actor each frame only while its proxy (actor +0x280) is in
                 * the world (+0xA5) and has moved (+0x108), or +0x82C is set.
                 * For Gaza's soldiers bullets passed through (0.339-0.340) */
                uint32_t body = 0;
                if ((strstr(line, "objectType=human") || strstr(line, "objectType=PLAYER")) &&
                    host->read_guest(host->context, actor + 0x76Cu, &body, 4) && k < (int)sizeof(line) - 300) {
                    uint32_t owner = 0, flags2 = 0, filter = 0, shape = 0, tfp = 0, stance = 0;
                    uint8_t inworld = 0, moved = 0, follow = 0, disable = 0, dead = 0;
                    float bp[3] = {0}, off[3] = {0};
                    host->read_guest(host->context, actor + 0x288u, &stance, 4);
                    host->read_guest(host->context, actor + 0x280u + 0xA5u, &inworld, 1);
                    host->read_guest(host->context, actor + 0x280u + 0x108u, &moved, 1);
                    host->read_guest(host->context, actor + 0x82Cu, &follow, 1);
                    host->read_guest(host->context, actor + 0x6C1u, &disable, 1);
                    host->read_guest(host->context, actor + 0x6B8u, &dead, 1);
                    host->read_guest(host->context, actor + 0x77Cu, off, 12);
                    if (body) {
                        host->read_guest(host->context, body + 0xA4u, &owner, 4);
                        host->read_guest(host->context, body + 0xB0u, &flags2, 4);
                        host->read_guest(host->context, body + 0x28u, &filter, 4);
                        host->read_guest(host->context, body + 0x0Cu, &shape, 4);
                        if (host->read_guest(host->context, body + 0x14u, &tfp, 4) && tfp)
                            host->read_guest(host->context, tfp + 0x50u, bp, 12);
                    }
                    /* 0.344: whether the bullet ray can find the body: it's only tested
                     * if the broadphase returns it, so the body's world (+0x08, the
                     * world is *0x37A3F0), its broadphase handle (+0x1C, type +0x24
                     * must be 1) and its simulation island (+0x44: +0x29 active,
                     * +0x24 index, -1 none). Gaza's unhittable heavy (0.343) had a
                     * right capsule following him, yet bullets passed through */
                    uint32_t bworld = 0, gworld = 0, bph = 0, btype = 0, island = 0, iidx = 0;
                    uint8_t iact = 0;
                    if (body) {
                        host->read_guest(host->context, body + 0x08u, &bworld, 4);
                        host->read_guest(host->context, 0x37A3F0u, &gworld, 4);
                        host->read_guest(host->context, body + 0x1Cu, &bph, 4);
                        host->read_guest(host->context, body + 0x24u, &btype, 4);
                        if (host->read_guest(host->context, body + 0x44u, &island, 4) && island) {
                            host->read_guest(host->context, island + 0x29u, &iact, 1);
                            host->read_guest(host->context, island + 0x24u, &iidx, 4);
                        }
                    }
                    k += snprintf(line + k, sizeof(line) - k, " inworld_body=%u broadphase=%08X type=%u island=%08X active=%u index=%d",
                                  body && bworld && bworld == gworld, bph, btype, island, iact, (int32_t)iidx);
                    const char *kind = shape == 0x37A320u ? "capsule" : shape == 0x37A380u ? "SLAB" :
                                       shape == 0x37A350u ? "long capsule" : shape == 0x37A2F0u ? "other capsule" : "?";
                    k += snprintf(line + k, sizeof(line) - k,
                                  " hitbox=%08X owner_ok=%u flags=%X layer=%u shape=%08X(%s) at (%.2f, %.2f, %.2f) offset (%.2f, %.2f, %.2f)"
                                  " stance=%u inworld=%u moved=%u follow=%u disable=%u dead=%u",
                                  body, owner == actor, flags2, filter & 0x1Fu, shape, body ? kind : "none",
                                  bp[0], bp[1], bp[2], off[0], off[1], off[2], stance, inworld, moved, follow, disable, dead);
                }
            }
            host->log(host->context, line);
            logged++;
        }
        append_output("probe: %u objects within 40 m written to the log", logged);
    } else if (!strcmp(cmd, "pick")) {
        unsigned k = (unsigned)atoi(arg);
        EnterCriticalSection(&lock);
        int ok = k >= 1 && k <= nearby_count;
        if (ok) {
            hud_pick = nearby[k - 1].handle;
            snprintf(hud_pick_label, sizeof(hud_pick_label), "%s", nearby[k - 1].name[0] ? nearby[k - 1].name
                                                                                            : nearby[k - 1].model);
        }
        LeaveCriticalSection(&lock);
        if (ok) append_output("picked %s: /up /down /left /right /here /remove", hud_pick_label);
        else append_output("/pick <number from /near>");
    } else if (!strcmp(cmd, "up") || !strcmp(cmd, "down") || !strcmp(cmd, "left") || !strcmp(cmd, "right") ||
               !strcmp(cmd, "here") || !strcmp(cmd, "remove")) {
        if (!hud_pick) { append_output("/near, then /pick <number> first"); return; }
        int kind = !strcmp(cmd, "up") ? EDIT_UP : !strcmp(cmd, "down") ? EDIT_DOWN : !strcmp(cmd, "left") ? EDIT_LEFT
                 : !strcmp(cmd, "right") ? EDIT_RIGHT : !strcmp(cmd, "here") ? EDIT_HERE : EDIT_REMOVE;
        do_edit(kind, hud_pick);
        hud_status();
        if (kind == EDIT_REMOVE) hud_pick = 0;
    } else if (!strcmp(cmd, "money")) {
        float amount = arg[0] ? (float)atof(arg) : 10000.0f;
        do_money(amount);
        hud_status();
    } else if (!strcmp(cmd, "objectives")) {
        hud_objectives();
    } else {
        append_output("/spawn <template>  /near  /probe  /pick <n>  /up /down /left /right /here /remove  /money [n]  /objectives");
    }
}

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
                if (typed[0] == '/') hud_command(typed + 1);
                else run_lua(0, typed);
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
    const char *title = typing ? "Foundry Console  (Lua or /help; Enter runs, Esc stops)"
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

/* The game's main heap (Heap_Allocate's, 0x645984: +00 base, +04 size, +08
 * bytes free, +10 free-block array of (address, size), +18 their count). A
 * streamed model or texture needs one free piece its size; when none is big
 * enough the game retries for ever (out of memory). Logged to plugins.log
 * every 5 s in play, and at once when the largest piece shrinks by 64 KB or
 * falls under 256 KB, so a freeze leaves the heap's last state in the logs. */
#define MAIN_HEAP 0x00645984u
static void heap_watch(double dt)
{
    static double since = 0, since_log = 1e9;
    static uint32_t last_largest = 0;
    since += dt;
    since_log += dt;
    if (since < 1.0) return;
    since = 0;
    uint32_t h[7] = {0};
    if (!host->read_guest(host->context, MAIN_HEAP, h, sizeof(h))) return;
    uint32_t count = h[6], largest = 0;
    if (!count || count > 20000) return;
    static uint32_t blocks[2 * 20000];
    if (!host->read_guest(host->context, h[4], blocks, count * 8)) return;
    for (uint32_t i = 0; i < count; i++)
        if (blocks[2 * i + 1] > largest) largest = blocks[2 * i + 1];
    int shrank = last_largest && largest + 65536 <= last_largest;
    if (since_log < 5.0 && !shrank && !(largest < 262144 && largest != last_largest)) return;
    char msg[260];
    int at = snprintf(msg, sizeof(msg), "heap: %u KB free of %u KB in %u pieces, largest %u KB (%u bytes)%s",
                      h[2] / 1024, h[1] / 1024, count, largest / 1024, largest, largest < 262144 ? " LOW" : "");
    /* The actor pool (0x31BDF8: placed objects' memory, ActorMemory_WriteReport's
     * totals) grows in 0x200-byte blocks (0x3A370: +1C bytes, +20 blocks) from
     * a heap of its own at +28 (laid out as the main heap's, it seems). Gaza ran
     * it out on the sixth level load of a run (0.298); logged here to see
     * whether it grows from level to level. */
    uint32_t pool[3] = {0}, ah[3] = {0};
    if (at > 0 && host->read_guest(host->context, 0x31BE14u, pool, 8) &&
        host->read_guest(host->context, 0x31BE20u, ah, sizeof(ah)) && ah[1] && ah[1] < 0x4000000u && ah[2] <= ah[1])
        snprintf(msg + at, sizeof(msg) - at, "; actor heap %u KB free of %u KB, pool %u blocks (%u KB)",
                 ah[2] / 1024, ah[1] / 1024, pool[1], pool[0] / 1024);
    host->log(host->context, msg);
    last_largest = largest;
    since_log = 0;
}

/* The actor pool's lists (0.307): 0x31BDF8 keeps each placed object's
 * 0x200-byte block on two lists (+0x68, +0x78), linked through a node near
 * the block's end: +0x1DC and +0x1E0 its links, +0x1E4 the block. The level
 * unload (0x174F60 -> 0x3A140) takes the first block, unlinks it and deletes
 * it until the list is empty; a block whose links were overwritten stays
 * first, and is deleted again and again (the stops after a visit to Gaza
 * with its drones). Walked once a second and as each level unloads: the
 * first broken link is logged with the block around it, so the log says
 * what wrote over it and when. Only reads. */
#define ACTOR_POOL 0x0031BDF8u
static int pool_bad_logged;
static double pool_level_time;

static void pool_dump(const char *what, uint32_t block)
{
    uint32_t w[128];
    char line[200];
    if (!block || !host->read_guest(host->context, block, w, sizeof(w))) {
        snprintf(line, sizeof(line), "actor pool: %s %08X can't be read", what, block);
        host->log(host->context, line);
        return;
    }
    snprintf(line, sizeof(line), "actor pool: %s %08X (vtable %08X):", what, block, w[0]);
    host->log(host->context, line);
    for (int r = 0; r < 16; r++) {
        int n = snprintf(line, sizeof(line), "  +%03X", r * 32);
        for (int k = 0; k < 8; k++) n += snprintf(line + n, sizeof(line) - n, " %08X", w[r * 8 + k]);
        host->log(host->context, line);
    }
    /* whose it is: words in it that are live world objects, by model */
    static uint32_t objs[OBJECT_SLOTS], handles[OBJECT_SLOTS];
    if (!host->read_guest(host->context, OBJECTS, objs, sizeof(objs)) ||
        !host->read_guest(host->context, OBJECT_HANDLES, handles, sizeof(handles))) return;
    int named = 0;
    for (int k = 0; k < 128 && named < 6; k++) {
        if (w[k] < 0x10000u) continue;
        for (unsigned i = 0; i < OBJECT_SLOTS; i++) {
            if (objs[i] != w[k]) continue;
            char m[48];
            object_model(objs[i], handles[i], m, sizeof(m));
            snprintf(line, sizeof(line), "  +%03X points at object %08X (handle %08X, model %s)", k * 4, objs[i],
                     handles[i], m[0] ? m : "?");
            host->log(host->context, line);
            named++;
            break;
        }
    }
}

static void pool_check(const char *when)
{
    char line[240];
    if (pool_bad_logged >= 2) return;
    for (int L = 0; L < 2; L++) {
        uint32_t head = ACTOR_POOL + (L ? 0x78u : 0x68u), h[4] = {0};
        if (!host->read_guest(host->context, head, h, sizeof(h))) return;
        uint32_t x = h[1], prev = head, n = 0;
        while (x != head && n < 20000) {
            uint32_t node[3] = {0}, back = 0;
            int ok = x && host->read_guest(host->context, x, node, sizeof(node)) &&
                     host->read_guest(host->context, node[1], &back, 4);
            /* each node: its +4 neighbour's +0 leads back to it, its +0 is the
             * one before, its +8 the block the node ends */
            const char *why = !ok ? "can't be read" : back != x ? "its next one doesn't lead back to it"
                            : node[0] != prev ? "it doesn't lead back to the one before"
                            : node[2] != x - 0x1DCu ? "its block isn't the one it ends" : NULL;
            if (why) {
                pool_bad_logged++;
                snprintf(line, sizeof(line), "actor pool: list %d broken %s (%.1f s into the level): node %08X after %08X, "
                         "links %08X %08X block %08X: %s; %u nodes walked, the list says %u",
                         L, when, pool_level_time, x, prev, node[0], node[1], node[2], why, n, h[3]);
                host->log(host->context, line);
                if (prev != head) pool_dump("the block before", prev - 0x1DCu);
                pool_dump("the block", x - 0x1DCu);
                if (ok && node[1] != head) pool_dump("the block after", node[1] - 0x1DCu);
                return;
            }
            prev = x;
            x = node[1];
            n++;
        }
    }
}

static void fly_update(double dt);     /* Foundry_FlyLoop's, below */
static unsigned flyer_count;
static void pool_watch(double dt)
{
    static double since;
    pool_level_time += dt;
    if ((since += dt) < 1.0) return;
    since = 0;
    pool_check("in play");
}

static int MERC_PLUGIN_CALL on_update(void *ud, const MercPluginUpdate *u)
{
    (void)ud;
    InterlockedExchange(&sample_playing, host->in_gameplay(host->context) != 0);
    if (sample_playing) heap_watch(u->wall_delta_seconds);
    if (sample_playing) pool_watch(u->wall_delta_seconds);
    if (sample_playing) fly_update(u->wall_delta_seconds);
    live_update(u->wall_delta_seconds);
    maptex_poll(u->wall_delta_seconds);
    maptext_poll(u->wall_delta_seconds);
    hq_dump_poll(u->wall_delta_seconds);
    if (host->in_gameplay(host->context)) ground_probe_poll(u->wall_delta_seconds);
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
#define LIVE_LINE 1600     /* a "paint" line carries a patch's alpha as hex: 90 characters a layer */
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

/* ---------- live terrain heights ----------
 * The loaded terrain's heights are heap buffers the game reads directly
 * (Terrain_GetCellHeight 0x146A10, the physics heightfield 0x146F30, the
 * ground sampler 0x1F1530): byte per cell, block-major in block x block
 * blocks, height = ((byte << shift) + base) * step with {u8 shift, pad,
 * s16 base} per block. Changing them moves collision at once; the drawn
 * ground is patches built on demand (Terrain_BuildPatch 0x21D650) and kept
 * in a 64-slot cache at *(g_Terrain + 0x14): u16 keys at +8, (patch a << 8)
 * + patch b, 0xFFFF for empty, and a dirty byte per slot at +0xC8. The game
 * empties a slot the same way (0x1F0080: key 0xFFFF, dirty 0), so the next
 * frame builds the patch again from the new heights. Untested in game. */
#define TERRAIN_CACHE_PTR (TERRAIN_OBJECT + 0x14u)
#define TERRAIN_PATCH_SHIFT 0x006432DCu     /* log2 of cells per patch (0x1EFFE0) */
#define TERRAIN_PATCHES 0x0064330Cu         /* patches per side (0x21E040's bound) */
#define CACHE_SLOTS 64
#define MAX_HEIGHT_EDITS 64

/* The check that starts a heights or paint line: a cell and its height (in
 * height units) as the editor's map has it. The game reports no map name,
 * so this is how an edit made on another map is refused here (within the
 * block's rounding). Returns 1 when the game's ground has that height. */
static int live_check(const char *what, char **next, uint32_t heights, uint32_t blocks, int size, int block)
{
    char *c = strtok_s(NULL, " ", next), *r = c ? strtok_s(NULL, " ", next) : NULL,
         *u = r ? strtok_s(NULL, " ", next) : NULL;
    if (!u) { live_result("%s: no check sample", what); return 0; }
    int cc = atoi(c) & (size - 1), cr = atoi(r) & (size - 1), want = atoi(u);
    int blk = (cr / block) * (size / block) + cc / block;
    uint8_t byte = 0, shift = 0;
    int16_t base = 0;
    host->read_guest(host->context, heights + (uint32_t)(blk * block * block + (cr % block) * block + cc % block), &byte, 1);
    host->read_guest(host->context, blocks + (uint32_t)blk * 4, &shift, 1);
    host->read_guest(host->context, blocks + (uint32_t)blk * 4 + 2, &base, 2);
    int have = ((int)byte << (shift & 31)) + base;
    if (abs(have - want) > (1 << (shift & 31))) {
        live_result("%s: the game's ground isn't the editor's map here (cell %d,%d is %d, the editor has %d); "
                    "nothing changed", what, cc, cr, have, want);
        return 0;
    }
    return 1;
}

static void live_heights(char **next)
{
    uint32_t heights = 0, blocks = 0;
    int16_t size = 0, block = 0;
    host->read_guest(host->context, TERRAIN_HEIGHTS, &heights, 4);
    host->read_guest(host->context, TERRAIN_BLOCKS, &blocks, 4);
    host->read_guest(host->context, TERRAIN_SIZE, &size, 2);
    host->read_guest(host->context, TERRAIN_BLOCK, &block, 2);
    if (!heights || !blocks || size <= 0 || block <= 0 || block > 64 || size % block) {
        live_result("heights: no terrain loaded here");
        return;
    }
    if (!live_check("heights", next, heights, blocks, size, block)) return;
    int cols[MAX_HEIGHT_EDITS], rows[MAX_HEIGHT_EDITS], units[MAX_HEIGHT_EDITS], n = 0;
    for (char *t; n < MAX_HEIGHT_EDITS && (t = strtok_s(NULL, " ", next)) != NULL; n++) {
        char *r = strtok_s(NULL, " ", next), *u = strtok_s(NULL, " ", next);
        if (!r || !u) break;
        cols[n] = atoi(t) & (size - 1);
        rows[n] = atoi(r) & (size - 1);
        units[n] = atoi(u);
    }
    if (!n) return;
    /* Each block touched: decode its cells, set the new ones, and encode it
     * again with the smallest shift that fits (as the build does). */
    int bpr = size / block, cells = block * block, done_blocks = 0;
    static uint8_t bytes[64 * 64];
    static int values[64 * 64];
    int handled[MAX_HEIGHT_EDITS] = {0};
    for (int i = 0; i < n; i++) {
        if (handled[i]) continue;
        int blk = (rows[i] / block) * bpr + cols[i] / block;
        uint32_t at = heights + (uint32_t)(blk * cells), info = blocks + (uint32_t)blk * 4;
        uint8_t shift = 0;
        int16_t base = 0;
        host->read_guest(host->context, at, bytes, (size_t)cells);
        host->read_guest(host->context, info, &shift, 1);
        host->read_guest(host->context, info + 2, &base, 2);
        for (int k = 0; k < cells; k++) values[k] = ((int)bytes[k] << (shift & 31)) + base;
        for (int j = i; j < n; j++) {
            if ((rows[j] / block) * bpr + cols[j] / block != blk) continue;
            values[(rows[j] % block) * block + cols[j] % block] = units[j];
            handled[j] = 1;
        }
        int lo = values[0], hi = values[0];
        for (int k = 1; k < cells; k++) { if (values[k] < lo) lo = values[k]; if (values[k] > hi) hi = values[k]; }
        if (lo < -32768 || hi > 32767) { live_result("heights: a value is out of range"); continue; }
        uint8_t ns = 0;
        while (((hi - lo) >> ns) > 255) ns++;
        for (int k = 0; k < cells; k++) bytes[k] = (uint8_t)((values[k] - lo) >> ns);
        int16_t nb = (int16_t)lo;
        host->write_guest(host->context, at, bytes, (size_t)cells);
        host->write_guest(host->context, info, &ns, 1);
        host->write_guest(host->context, info + 2, &nb, 2);
        done_blocks++;
    }
    /* The drawn patches over the changed cells and their neighbours (normals
     * and seams), either way round (which patch coordinate is the key's high
     * byte isn't settled): emptied so they're built again. */
    uint32_t cache = 0, pshift = 0, patches = 0;
    host->read_guest(host->context, TERRAIN_CACHE_PTR, &cache, 4);
    host->read_guest(host->context, TERRAIN_PATCH_SHIFT, &pshift, 4);
    host->read_guest(host->context, TERRAIN_PATCHES, &patches, 4);
    uint16_t keys[CACHE_SLOTS];
    int redrawn = 0, sane = cache && pshift < 8 && patches > 0 && patches <= 256 &&
                  host->read_guest(host->context, cache + 8, keys, sizeof(keys));
    for (int k = 0; sane && k < CACHE_SLOTS; k++)      /* only a cache that looks like one */
        if (keys[k] != 0xFFFF && ((uint32_t)(keys[k] >> 8) >= patches || (uint32_t)(keys[k] & 0xFF) >= patches)) sane = 0;
    if (sane) {
        for (int k = 0; k < CACHE_SLOTS; k++) {
            if (keys[k] == 0xFFFF) continue;
            int a = keys[k] >> 8, b = keys[k] & 0xFF, hit = 0;
            for (int i = 0; i < n && !hit; i++) {
                int pc = cols[i] >> pshift, pr = rows[i] >> pshift;
                hit = (abs(a - pc) <= 1 && abs(b - pr) <= 1) || (abs(a - pr) <= 1 && abs(b - pc) <= 1);
            }
            if (!hit) continue;
            uint16_t empty = 0xFFFF;
            uint8_t clean = 0;
            host->write_guest(host->context, cache + 8 + (uint32_t)k * 2, &empty, 2);
            host->write_guest(host->context, cache + 0xC8 + (uint32_t)k, &clean, 1);
            redrawn++;
        }
    }
    live_result(sane ? "heights: %d samples in %d blocks; %d drawn patches rebuilt"
                     : "heights: %d samples in %d blocks (the drawn ground's cache wasn't where expected: it shows when you move away and back)",
                n, done_blocks, redrawn);
}

/* ---------- live ground paint ----------
 * Each 8 x 8-cell patch has a 16-byte record (Terrain_Load, 0x1F07A0): the
 * array at *0x64314C, *0x6432F8 of them, row by row. +0: a pointer to the
 * patch's ALPH blob (0 when it has one layer or none); +8: its layer mask
 * (PTCH). A blob is u16 stored, u16 unpacked, then the data: raw when stored
 * == unpacked (Terrain_BuildPatch 0x21D650 copies it), 45 bytes a layer in
 * ascending bit order (9 rows of 5 bytes of nibbles). The editor sends a
 * patch's new mask and raw data, encoded as the build encodes paint
 * (terrain_paint.encode_patch on its server). The blob goes where the old one
 * was when it fits, else in a block from the game's allocator (0x1F7230, the
 * main heap, as Terrain_Load's own); then the mask, then the patch's drawn
 * cache slot is emptied. Only layers the map's ground already uses anywhere
 * are taken: a layer its LTEX doesn't define might crash the drawing.
 * Untested in game. */
#define TERRAIN_PATCH_RECORDS 0x0064314Cu
#define TERRAIN_PATCH_COUNT 0x006432F8u
#define GAME_ALLOC 0x001F7230u
#define GAME_ALLOC_TAG 0x002FC9D4u        /* the tag string Terrain_Load's ALPH buffer uses */

static int hexval(char ch)
{
    return ch >= '0' && ch <= '9' ? ch - '0' : ch >= 'a' && ch <= 'f' ? ch - 'a' + 10 :
           ch >= 'A' && ch <= 'F' ? ch - 'A' + 10 : -1;
}

static void live_paint(char **next)
{
    uint32_t heights = 0, blocks = 0, records = 0, count = 0, patches = 0, cache = 0;
    int16_t size = 0, block = 0;
    host->read_guest(host->context, TERRAIN_HEIGHTS, &heights, 4);
    host->read_guest(host->context, TERRAIN_BLOCKS, &blocks, 4);
    host->read_guest(host->context, TERRAIN_SIZE, &size, 2);
    host->read_guest(host->context, TERRAIN_BLOCK, &block, 2);
    host->read_guest(host->context, TERRAIN_PATCH_RECORDS, &records, 4);
    host->read_guest(host->context, TERRAIN_PATCH_COUNT, &count, 4);
    host->read_guest(host->context, TERRAIN_PATCHES, &patches, 4);
    if (!heights || !blocks || !records || size <= 0 || block <= 0 || block > 64 || size % block ||
        !patches || patches > 256 || count != patches * patches) {
        live_result("paint: no terrain loaded here");
        return;
    }
    if (!live_check("paint", next, heights, blocks, size, block)) return;
    char *c = strtok_s(NULL, " ", next), *r = c ? strtok_s(NULL, " ", next) : NULL,
         *m = r ? strtok_s(NULL, " ", next) : NULL, *hex = m ? strtok_s(NULL, " ", next) : NULL;
    if (!m) { live_result("paint: col row mask [alpha] expected"); return; }
    uint32_t col = (uint32_t)atoi(c), row = (uint32_t)atoi(r), mask = (uint32_t)strtoul(m, NULL, 16) & 0xFFFF;
    if (col >= patches || row >= patches) { live_result("paint: no patch %u,%u", col, row); return; }
    /* the layers the ground uses anywhere (each defined by its LTEX) */
    static uint32_t used_for, used;
    if (used_for != records) {
        used = 0;
        static uint8_t recs[256 * 16];
        for (uint32_t i = 0; i < count; i += 256) {
            uint32_t n = count - i < 256 ? count - i : 256;
            host->read_guest(host->context, records + i * 16, recs, n * 16);
            for (uint32_t k = 0; k < n; k++) used |= (uint32_t)recs[k * 16 + 8] | (uint32_t)recs[k * 16 + 9] << 8;
        }
        used_for = records;
    }
    if (mask & ~used) {
        live_result("paint: layer(s) %04X aren't on the game's ground yet (an added layer shows after the next start)",
                    mask & ~used);
        return;
    }
    int layers = 0;
    for (int b = 0; b < 16; b++) layers += (mask >> b) & 1;
    uint32_t rec = records + (row * patches + col) * 16;
    uint32_t blob = 0;
    if (layers > 1) {
        static uint8_t data[4 + 45 * 16];
        size_t want = (size_t)45 * layers, len = hex ? strlen(hex) / 2 : 0;
        if (len != want) { live_result("paint: patch %u,%u: %u bytes of alpha for %d layers", col, row, (unsigned)len, layers); return; }
        for (size_t i = 0; i < len; i++) {
            int hi = hexval(hex[2 * i]), lo = hexval(hex[2 * i + 1]);
            if (hi < 0 || lo < 0) { live_result("paint: bad alpha"); return; }
            data[4 + i] = (uint8_t)(hi << 4 | lo);
        }
        uint16_t n16 = (uint16_t)len;
        memcpy(data, &n16, 2);
        memcpy(data + 2, &n16, 2);
        uint32_t old = 0;
        uint16_t stored = 0;
        host->read_guest(host->context, rec, &old, 4);
        if (old) host->read_guest(host->context, old, &stored, 2);
        if (old && (size_t)stored + 4 >= len + 4) {
            blob = old;                       /* fits where the old one was */
        } else {
            MercGuestCall call;
            uint32_t args[3] = {(uint32_t)len + 4, 0, GAME_ALLOC_TAG};
            if (!guest(GAME_ALLOC, 0, 3, args, &call) || !call.eax) { live_result("paint: no memory for patch %u,%u", col, row); return; }
            blob = call.eax;                  /* (kept until the level unloads; a little each time a patch grows) */
        }
        host->write_guest(host->context, blob, data, len + 4);
    }
    host->write_guest(host->context, rec, &blob, 4);
    uint16_t m16 = (uint16_t)mask;
    host->write_guest(host->context, rec + 8, &m16, 2);
    /* its drawn patch, built again next frame */
    uint16_t keys[CACHE_SLOTS];
    int redrawn = 0;
    host->read_guest(host->context, TERRAIN_CACHE_PTR, &cache, 4);
    if (cache && host->read_guest(host->context, cache + 8, keys, sizeof(keys))) {
        for (int k = 0; k < CACHE_SLOTS; k++) {
            if (keys[k] != (uint16_t)((col << 8) | row) && keys[k] != (uint16_t)((row << 8) | col)) continue;
            uint16_t empty = 0xFFFF;
            uint8_t clean = 0;
            host->write_guest(host->context, cache + 8 + (uint32_t)k * 2, &empty, 2);
            host->write_guest(host->context, cache + 0xC8 + (uint32_t)k, &clean, 1);
            redrawn++;
        }
    }
    live_result("paint: patch %u,%u, %d layer(s)%s", col, row, layers, redrawn ? ", drawn again" : "");
}

static void live_run(char *line)
{
    char *next = NULL;
    char *op = strtok_s(line, " ", &next);
    if (!op) return;
    if (!strcmp(op, "lua")) {
        /* a line of Lua in the newest script's state (Utility_ReadLevelFile, weather, music...) */
        char result[400];
        int expression = 0;
        const char *code = next ? next : "";
        int status = lua_line(0, code, result, sizeof(result), &expression);
        live_result(status < 0 ? "lua: %s" : status ? "lua error: %s" : expression ? "lua = %s" : "lua ok%s",
                    status == 0 && !expression ? "" : result);
        return;
    }
    if (!strcmp(op, "heights")) { live_heights(&next); return; }
    if (!strcmp(op, "paint")) { live_paint(&next); return; }
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

/* Foundry_SetHandling('<vehicle>', '<physics key>', value): change a
 * vehicle's handling while the game runs; returns what it did, as a string.
 * The vehicle is a name (or 'player0''s...) as Lua_ArgActor reads it; its
 * actor (+0x24) holds its physics, the class told by the actor's vtable
 * (docs/VEHICLES.md, Changing handling live): wheeled 0x2E16D0, drivetrain at
 * +0x360; helicopter 0x2E1F08, physics at +0x2C8; tank 0x2E3760, at +0x2E4.
 * The key is any physics* name the class's setter takes (e.g.
 * physicsenginetorque). Wheel friction is copied to the tyres only at setup,
 * so a friction change may not show until the vehicle is spawned again. */
#define ACTOR_VT_WHEELED 0x2E16D0u
#define ACTOR_VT_HELICOPTER 0x2E1F08u
#define ACTOR_VT_TANK 0x2E3760u

static int MERC_PLUGIN_CALL cmd_set_handling(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    char key[64] = {0}, out[200];
    uint32_t args[2] = {L, 1}, obj = 0, actor = 0, vt = 0;
    if (guest(MF_FN_Lua_ArgActor, 0, 2, args, &c)) obj = c.eax;
    args[1] = 2;
    if (guest(MF_FN_Lua_ArgString, 0, 2, args, &c) && c.eax)
        for (unsigned i = 0; i < sizeof(key) - 1; i++)
            if (!host->read_guest(host->context, c.eax + i, &key[i], 1) || !key[i]) break;
    args[1] = 3;
    float value = guest(MF_FN_Lua_ArgNumber, 0, 2, args, &c) ? (float)c.st0 : 0.0f;
    if (obj) host->read_guest(host->context, obj + MF_OFF_WorldObject_actor, &actor, 4);
    if (actor) host->read_guest(host->context, actor, &vt, 4);
    uint32_t set = 0, get = 0, self = 0;
    const char *kind = "";
    if (vt == ACTOR_VT_WHEELED) {
        set = MF_FN_Drivetrain_SetPhysicsValue; get = MF_FN_Drivetrain_GetPhysicsValue;
        self = actor + 0x360; kind = "wheeled";
    } else if (vt == ACTOR_VT_HELICOPTER) {
        set = MF_FN_Helicopter_SetPhysicsValue; self = actor + 0x2C8; kind = "helicopter";
    } else if (vt == ACTOR_VT_TANK) {
        set = MF_FN_TrackedVehicle_SetPhysicsValue; get = MF_FN_TrackedVehicle_GetPhysicsValue;
        self = actor + 0x2E4; kind = "tank";
    }
    uint32_t hash = hash_name(key);
    if (!obj)
        snprintf(out, sizeof(out), "no such vehicle");
    else if (!set)
        snprintf(out, sizeof(out), "not a wheeled vehicle, helicopter or tank (actor vtable %08X)", vt);
    else if (!key[0])
        snprintf(out, sizeof(out), "give a physics* key, e.g. physicsenginetorque");
    else {
        float before = 0;
        int had = get && guest(get, self, 1, &hash, &c);
        if (had) before = (float)c.st0;
        uint32_t bits;
        memcpy(&bits, &value, 4);
        uint32_t sargs[2] = {hash, bits};
        int ok = guest(set, self, 2, sargs, &c);
        if (!ok)
            snprintf(out, sizeof(out), "%s: the setter refused the call (%s)", kind, c.error);
        else if (had)
            snprintf(out, sizeof(out), "%s: %s %g -> %g", kind, key, before, value);
        else
            snprintf(out, sizeof(out), "%s: %s set to %g", kind, key, value);
    }
    uint32_t scratch, size;
    if (host->guest_scratch(host->context, &scratch, &size) && size > sizeof(out)) {
        host->write_guest(host->context, scratch, out, strlen(out) + 1);
        uint32_t push[2] = {L, scratch};
        guest(MF_FN_lua_pushstring, 0, 2, push, &c);
        return 1;
    }
    return 0;
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

/* ---- HQ dumps: measured numbers for HQ rooms ----
 * Foundry_Log(text) writes a script's line to plugins.log (a new map's
 * interiors log their dumps with it: foundry_<map>_HqDump). Retail's HQ
 * briefing is measured from here: from Briefing_Init, every 2 s for 30 s and
 * once at Briefing_Deinit, a line of Lua in the newest script (the
 * briefing's) gives the player's, the chair's, the exit's and the scene's
 * positions and yaws (both are attached to the scene's hp_player_chair, so
 * these are retail's real player-to-chair numbers). */
static int MERC_PLUGIN_CALL cmd_log(void *ud, uint32_t L)
{
    (void)ud;
    char text[900];
    lua_arg_text(L, 1, text, sizeof(text));
    host->log(host->context, text);
    push_text(L, "logged");
    return 1;
}

#define RETAIL_HQ_DUMP \
    "(function() local function r(v) if not v then return 'nil' end return Utility_MathFloor(v * 100 + 0.5) / 100 end " \
    "local function at(n) if not Utility_DoesActorExist(n) then return n .. '=none  ' end " \
    "local x, y, z = Actor_GetPosition(n) return n .. '=(' .. r(x) .. ', ' .. r(y) .. ', ' .. r(z) .. ') yaw ' .. r(Actor_GetYaw(n)) .. '  ' end " \
    "local s = at('player0') .. at('chair') .. at('starter_trigger') .. at('exit') .. at('boss') .. at('starter') " \
    "local sc = Briefing_GetSceneActor and Briefing_GetSceneActor() " \
    "if sc then local x, y, z = Actor_GetPosition(sc) s = s .. 'scene=(' .. r(x) .. ', ' .. r(y) .. ', ' .. r(z) .. ') yaw ' .. r(Actor_GetYaw(sc)) .. '  ' end " \
    "local x, y, z = Actor_GetPosition('player0') " \
    "if x and Utility_GetTerrainHeightAtXZ then s = s .. 'terrain ' .. r(Utility_GetTerrainHeightAtXZ(x, z)) end " \
    "return s end)()"

static double brief_dump_t = -1.0, brief_dump_next;
static int brief_dump_final;

static void retail_hq_dump(const char *when)
{
    char result[1024], line[1200];
    int expression = 0;
    int st = lua_line(0, RETAIL_HQ_DUMP, result, sizeof(result), &expression);
    snprintf(line, sizeof(line), "hq dump retail %s: %s%s", when, st ? "(couldn't run) " : "", result);
    host->log(host->context, line);
}

static void MERC_PLUGIN_CALL briefing_init_after(void *ud, MercHookContext *c)
{
    (void)ud; (void)c;
    brief_dump_t = 0.0;
    brief_dump_next = 1.0;
    host->log(host->context, "hq dump retail: briefing started");
}

static void MERC_PLUGIN_CALL briefing_deinit_before(void *ud, MercHookContext *c)
{
    (void)ud; (void)c;
    if (brief_dump_t >= 0.0) brief_dump_final = 1;   /* dumped from the next update, outside the hook */
    brief_dump_t = -1.0;
}

/* ---- Ground probe: helicopters against the ground (Gaza's extraction
 * Black Hawk settled about 2 m up at every landing zone) ----
 * Every 2 s in gameplay, each helicopter within 100 m (a nearby object whose
 * name or model says blackhawk, chinook, apache, heli, hind or hip): its
 * position, the terrain's height under it (Utility_GetTerrainHeightAtXZ, the
 * engine's own sampler), the gap, and the objects within 10 m of it across
 * (what it might be standing on), to plugins.log. The player's gap once per
 * probe as the reference. A helicopter is logged while it moves and once
 * more when it has settled. */
static double ground_probe_t;
typedef struct { uint32_t handle; float y; int settled; } ProbeSeen;
static ProbeSeen probe_seen[16];

static int is_helicopter(const Nearby *e)
{
    static const char *keys[] = {"blackhawk", "chinook", "apache", "heli", "hind", "_hip", "littlebird"};
    char low[100];
    snprintf(low, sizeof(low), "%s %s", e->name, e->model);
    for (char *p = low; *p; p++) *p = (char)tolower((unsigned char)*p);
    for (unsigned i = 0; i < sizeof(keys) / sizeof(keys[0]); i++)
        if (strstr(low, keys[i])) return 1;
    return 0;
}

static int terrain_height_at(float x, float z, float *out)
{
    char line[96], result[128];
    int expression = 0;
    snprintf(line, sizeof(line), "Utility_GetTerrainHeightAtXZ(%.3f, %.3f)", x, z);
    if (lua_line(0, line, result, sizeof(result), &expression) != 0) return 0;
    char *end = NULL;
    double v = strtod(result, &end);
    if (end == result) return 0;
    *out = (float)v;
    return 1;
}

/* Card probe (Gaza's card men drew retail's grey-jacketed card man although
 * each is a copy model, gaza_card_<suit>NN, drawing textures of its own):
 * once per card man within range, the model his object asks for
 * (GeometryFile) and whether that model's _head and _body textures are in
 * the game's texture table (Texture_Find's), with the texture pointers. */
static uint32_t card_seen[64];
static int texture_slot(uint32_t key, uint32_t *values);
static void card_probe(const Nearby *list, unsigned n)
{
    for (unsigned i = 0; i < n; i++) {
        const Nearby *e = &list[i];
        uint32_t key;
        if (e->name[0]) {
            if ((!strstr(e->name, "card") && !strstr(e->name, "deck_")) || strstr(e->name, "_guard") ||
                strstr(e->name, "_prop") || strstr(e->name, "_pickup")) continue;
            key = e->handle;
        } else {
            /* an unnamed character drawn as a Gaza copy (the IDF's soldiers):
             * once per model */
            if (strncmp(e->model, "gaza_", 5) || (!strstr(e->model, "_hum_") && !strstr(e->model, "idf"))) continue;
            key = hash_name(e->model) | 1u;
        }
        unsigned k;
        for (k = 0; k < 64 && card_seen[k] && card_seen[k] != key; k++) {}
        if (k == 64 || card_seen[k] == key) continue;
        card_seen[k] = key;
        char model[48], tex[64], line[400];
        object_model(e->object, e->handle, model, sizeof(model));
        int len = snprintf(line, sizeof(line), "card probe: %s draws %s", e->name[0] ? e->name : "(unnamed)", model[0] ? model : "(no GeometryFile)");
        static const char *parts[] = {"_head", "_body"};
        for (unsigned p = 0; p < 2; p++) {
            uint32_t values = 0, tp = 0;
            snprintf(tex, sizeof(tex), "%s%s", model, parts[p]);
            int at = model[0] ? texture_slot(hash_name(tex), &values) : -1;
            if (at >= 0) host->read_guest(host->context, values + 4 * (uint32_t)at, &tp, 4);
            len += snprintf(line + len, sizeof(line) - (size_t)len, "; %s %s", tex,
                            at < 0 ? "not in the texture table" : tp ? "loaded" : "named, no texture");
        }
        host->log(host->context, line);
    }
}

static void ground_probe_poll(double dt)
{
    if ((ground_probe_t += dt) < 2.0) return;
    ground_probe_t = 0;
    scan_nearby();
    static Nearby list[MAX_NEARBY];
    unsigned n;
    EnterCriticalSection(&lock);
    n = nearby_count;
    memcpy(list, nearby, n * sizeof(Nearby));
    LeaveCriticalSection(&lock);
    card_probe(list, n);
    for (unsigned i = 0; i < n; i++) {
        const Nearby *e = &list[i];
        if (!is_helicopter(e)) continue;
        ProbeSeen *s = NULL;
        for (unsigned k = 0; k < 16 && !s; k++) if (probe_seen[k].handle == e->handle) s = &probe_seen[k];
        if (!s) {
            for (unsigned k = 0; k < 16 && !s; k++) if (!probe_seen[k].handle) s = &probe_seen[k];
            if (!s) { s = &probe_seen[0]; }
            s->handle = e->handle; s->y = -99999.0f; s->settled = 0;
        }
        int still = fabsf(e->pos[1] - s->y) < 0.05f;
        if (still && s->settled) continue;           /* logged once settled */
        s->settled = still;
        s->y = e->pos[1];
        float g = 0;
        char line[900];
        int len = terrain_height_at(e->pos[0], e->pos[2], &g)
            ? snprintf(line, sizeof(line), "ground probe: %s%s (%.2f, %.2f, %.2f)%s: terrain %.2f, %.2f above it",
                       e->name[0] ? e->name : e->model, "", e->pos[0], e->pos[1], e->pos[2],
                       still ? " settled" : "", g, e->pos[1] - g)
            : snprintf(line, sizeof(line), "ground probe: %s (%.2f, %.2f, %.2f): terrain unknown",
                       e->name[0] ? e->name : e->model, e->pos[0], e->pos[1], e->pos[2]);
        /* what's within 10 m across of it */
        for (unsigned j = 0; j < n && len < (int)sizeof(line) - 80; j++) {
            const Nearby *o = &list[j];
            if (o == e) continue;
            float dx = o->pos[0] - e->pos[0], dz = o->pos[2] - e->pos[2];
            if (dx * dx + dz * dz > 100.0f) continue;
            len += snprintf(line + len, sizeof(line) - (size_t)len, " | %s y %.2f", o->name[0] ? o->name : o->model,
                            o->pos[1]);
        }
        host->log(host->context, line);
        float pg = 0;
        uint32_t player = find_player();
        float m[16];
        if (player && host->read_guest(host->context, player + MF_OFF_Actor_transform, m, sizeof(m)) &&
            terrain_height_at(m[12], m[14], &pg)) {
            snprintf(line, sizeof(line), "ground probe: player (%.2f, %.2f, %.2f): terrain %.2f, %.2f above it",
                     m[12], m[13], m[14], pg, m[13] - pg);
            host->log(host->context, line);
        }
    }
}

static void hq_dump_poll(double dt)
{
    char when[32];
    if (brief_dump_final) {
        brief_dump_final = 0;
        retail_hq_dump("leaving");
    }
    if (brief_dump_t < 0.0) return;
    brief_dump_t += dt;
    if (brief_dump_t >= brief_dump_next && brief_dump_next <= 30.0) {
        snprintf(when, sizeof(when), "%.0f s", brief_dump_next);
        retail_hq_dump(when);
        brief_dump_next += 2.0;
    }
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

/* ---- Text of a map's own (Foundry_MapText) ----
 * The game's strings are one hash table, Registry_Find(*g_StringTables)
 * (StringTable_GetAscii, 0x1211B0: mask at +4 (count - 1), UTF-16 text
 * pointers at +0xC, key hashes at +0x10, probed from key & mask on, as
 * HashTable_Find). A mod's new map asks, from its master script, for some
 * keys to show another key's text while it's loaded (the build adds those
 * texts: text/*.json and factions.json with "map"); the next level load
 * puts them back. */
#define MAPTEXT_MAX 4096
/* by key: the table may be rebuilt; other: the key whose text it shows */
typedef struct { uint32_t key, original, replaced, other; } MapTextSwap;
static MapTextSwap maptext[MAPTEXT_MAX];
static unsigned maptext_count;
static unsigned maptext_missing;       /* asked for, but a key wasn't in the table */
static char maptext_missed[96];        /* the first such key, for the log */

static uint32_t string_table(void)
{
    uint32_t name = 0, args[1];
    MercGuestCall c;
    if (!host->read_guest(host->context, MF_VAR_g_StringTables, &name, 4) || !name) return 0;
    args[0] = name;
    return guest(MF_FN_Registry_Find, 0, 1, args, &c) ? c.eax : 0;
}

/* the slot holding key in the table, or -1 */
static int string_slot(uint32_t table, uint32_t key, uint32_t *values)
{
    uint32_t count = 0, keys = 0, k = 0;
    if (!key || !host->read_guest(host->context, table + 4, &count, 4) || !count || (count & (count - 1)) ||
        !host->read_guest(host->context, table + 0xC, values, 4) ||
        !host->read_guest(host->context, table + 0x10, &keys, 4)) return -1;
    uint32_t mask = count - 1, i = key & mask;
    for (uint32_t n = 0; n < count; n++, i = (i + 1) & mask) {
        if (!host->read_guest(host->context, keys + 4 * i, &k, 4) || !k) return -1;
        if (k == key) return (int)i;
    }
    return -1;
}

static void maptext_restore(void)
{
    unsigned put = 0;
    /* the table as it is now (strings merged in since can grow and re-hash
     * it: each key is looked up again) */
    uint32_t table = maptext_count ? string_table() : 0;
    for (unsigned i = maptext_count; table && i-- > 0;) {
        MapTextSwap *s = &maptext[i];
        uint32_t values = 0, now = 0;
        int slot = string_slot(table, s->key, &values);
        if (slot >= 0 && host->read_guest(host->context, values + 4 * (uint32_t)slot, &now, 4) &&
            now == s->replaced) {
            host->write_guest(host->context, values + 4 * (uint32_t)slot, &s->original, 4);
            put++;
        }
    }
    if (maptext_count || maptext_missing) {
        char line[256];
        snprintf(line, sizeof(line), "map text: %u of %u strings put back (the rest no longer held the map's "
                 "text); %u asked for weren't in the string table (first: %s)", put, maptext_count,
                 maptext_missing, maptext_missing ? maptext_missed : "none");
        host->log(host->context, line);
    }
    maptext_count = 0;
    maptext_missing = 0;
}

/* ---- HUD art of a map's own (Foundry_MapTexture) ----
 * HUD and PDA sprites are 0x20-byte objects (constructor 0x216E60: +0 the
 * sprite's name hash, +4 its texture's hash, then UVs over the texture's
 * size) in a hash map at MEM32(0x842E20) + 0x10 (+0 size, +8 values: the
 * sprite objects, +0xC keys), filled by 0x108520. Each frame the drawer
 * (0x20B900, then the renderer 0x20DB40) looks the sprite up and turns its
 * +4 hash into a texture with Texture_Find (0xC2740), so a new hash there
 * draws another (loaded) texture, the same part of it. */
#define SPRITE_MAP_PTR 0x842E20u
#define MAPSPRITE_MAX 512
typedef struct { uint32_t sprite, original, replaced; } MapSpriteSwap;
static MapSpriteSwap mapsprite[MAPSPRITE_MAX];
static unsigned mapsprite_count;

static int u32_order(const void *a, const void *b)
{
    uint32_t x = *(const uint32_t *)a, y = *(const uint32_t *)b;
    return x < y ? -1 : x > y;
}

/* The swaps undone, but only on sprites still in the game's sprite table:
 * this runs as the next level loads, after the old one is unloaded, and a
 * sprite freed with it kept the swapped hash in its memory, so the check of
 * that word alone passed and the original was written into memory the new
 * level had taken. That was the actor pool's broken link (0.310: one word,
 * +0x1E0 of an SW tree's block, the original hash of a Gaza sprite that had
 * been at the block's node, 0.5 s into SW's load after Gaza): the level-load
 * stops after a Gaza visit. */
static void mapsprite_restore(void)
{
    unsigned put = 0, gone = 0;
    uint32_t map = 0, size = 0, values = 0, *live = NULL;
    if (mapsprite_count && host->read_guest(host->context, SPRITE_MAP_PTR, &map, 4) && map &&
        host->read_guest(host->context, map + 0x10, &size, 4) && size && size <= 0x100000 &&
        host->read_guest(host->context, map + 0x18, &values, 4) && values &&
        (live = (uint32_t *)malloc(4 * (size_t)size)) != NULL &&
        host->read_guest(host->context, values, live, 4 * size))
        qsort(live, size, 4, u32_order);
    else {
        free(live);
        live = NULL;
    }
    for (unsigned i = mapsprite_count; i-- > 0;) {
        uint32_t now = 0, sp = mapsprite[i].sprite;
        if (!live || !bsearch(&sp, live, size, 4, u32_order)) {
            gone++;
            continue;
        }
        if (host->read_guest(host->context, sp + 4, &now, 4) && now == mapsprite[i].replaced) {
            host->write_guest(host->context, sp + 4, &mapsprite[i].original, 4);
            put++;
        }
    }
    free(live);
    if (mapsprite_count) {
        char line[140];
        snprintf(line, sizeof(line), "map HUD art: %u of %u sprites put back (%u no longer in the game's sprite table, "
                 "left alone)", put, mapsprite_count, gone);
        host->log(host->context, line);
    }
    mapsprite_count = 0;
}

/* The game keeps its own HUD textures resident: level set-up (0x180DE2)
 * adds each one's hash to a keep list with 0x205D80(hash, 0) (lists per
 * category at MEM32(0x7934C0)). A texture only in a map's load list isn't,
 * and Texture_Find (0xC2740, __thiscall, this unused, ret 4) gives 0 for a
 * texture that isn't loaded, so its sprites draw untextured. So the other
 * texture is kept resident too, and the sprites switch once it's loaded. */
#define TEXTURE_KEEP 0x205D80u
#define TEXTURE_FIND 0xC2740u
#define TEXTURE_STREAMER 0x7934C8u
#define TEXTURE_REQUEST 0x1F7A70u
#define TEXTURE_RELEASE 0x1F7AF0u
#define TEXREQ_MAX 128
static uint32_t texreq[TEXREQ_MAX];
static unsigned texreq_count;

static void texreq_release(void)
{
    MercGuestCall c;
    for (unsigned i = 0; i < texreq_count; i++) {
        uint32_t args[2] = {texreq[i], 0};
        guest(TEXTURE_RELEASE, TEXTURE_STREAMER, 2, args, &c);
    }
    texreq_count = 0;
}
#define MAPTEX_PENDING_MAX 128
typedef struct { uint32_t from, to; double since; } MapTexPending;
static MapTexPending maptex_pending[MAPTEX_PENDING_MAX];
static unsigned maptex_pending_count;
static double maptex_clock;           /* seconds of play since the plugin started */

static int texture_loaded(uint32_t hash)
{
    MercGuestCall c;
    uint32_t self = 0, args[1] = {hash};
    host->read_guest(host->context, 0x7934C0u, &self, 4);
    return guest(TEXTURE_FIND, self, 1, args, &c) && c.eax != 0;
}

static unsigned maptex_swap(uint32_t want, uint32_t other)
{
    uint32_t map = 0, size = 0, values = 0, keys = 0;
    if (!host->read_guest(host->context, SPRITE_MAP_PTR, &map, 4) || !map) return 0;
    map += 0x10;
    if (!host->read_guest(host->context, map, &size, 4) || !size || size > 0x100000 ||
        !host->read_guest(host->context, map + 8, &values, 4) ||
        !host->read_guest(host->context, map + 0xC, &keys, 4)) return 0;
    unsigned n = 0;
    for (uint32_t i = 0; i < size; i++) {
        uint32_t key = 0, sprite = 0, tex = 0;
        if (!host->read_guest(host->context, keys + 4 * i, &key, 4) || !key) continue;
        if (!host->read_guest(host->context, values + 4 * i, &sprite, 4) || !sprite) continue;
        if (!host->read_guest(host->context, sprite + 4, &tex, 4) || tex != want) continue;
        if (mapsprite_count == MAPSPRITE_MAX) break;
        mapsprite[mapsprite_count++] = (MapSpriteSwap){sprite, tex, other};
        host->write_guest(host->context, sprite + 4, &other, 4);
        n++;
    }
    return n;
}

/* Textures drawn by name hash, not through a sprite (the PDA's card
 * portraits, "%s%i_face", RsDataPodDisplay 0xC7340 -> 0xC5900 ->
 * Texture_Find): Texture_Find's table (0x793510 count, a power of two;
 * 0x793518 values, the textures; 0x79351C keys, linear probing as
 * HashTable_Find) gets the other texture under the game's key until the
 * next level loads. Looked up by key each time; every 2 s a key back on the
 * game's texture gets the map's again. */
#define TEXTABLE_COUNT 0x793510u
#define TEXTABLE_VALUES 0x793518u
#define TEXTABLE_KEYS 0x79351Cu
#define MAPTEXALIAS_MAX 128
typedef struct { uint32_t key, original, replaced, other; } MapTexAlias;
static MapTexAlias maptexalias[MAPTEXALIAS_MAX];
static unsigned maptexalias_count;
static double maptexalias_timer;

static int texture_slot(uint32_t key, uint32_t *values)
{
    uint32_t count = 0, keys = 0, k = 0;
    if (!key || !host->read_guest(host->context, TEXTABLE_COUNT, &count, 4) || !count || (count & (count - 1)) ||
        !host->read_guest(host->context, TEXTABLE_VALUES, values, 4) ||
        !host->read_guest(host->context, TEXTABLE_KEYS, &keys, 4)) return -1;
    uint32_t mask = count - 1, i = key & mask;
    for (uint32_t n = 0; n < count; n++, i = (i + 1) & mask) {
        if (!host->read_guest(host->context, keys + 4 * i, &k, 4) || !k) return -1;
        if (k == key) return (int)i;
    }
    return -1;
}

/* key's texture becomes other's: 1, or 0 if either isn't in the table */
static int maptexalias_set(uint32_t key, uint32_t other)
{
    uint32_t values = 0, ovalues = 0, mine = 0, theirs = 0;
    int at = texture_slot(key, &values), from = texture_slot(other, &ovalues);
    if (at < 0 || from < 0 ||
        !host->read_guest(host->context, ovalues + 4 * (uint32_t)from, &theirs, 4) || !theirs ||
        !host->read_guest(host->context, values + 4 * (uint32_t)at, &mine, 4)) return 0;
    unsigned i;
    for (i = 0; i < maptexalias_count; i++)
        if (maptexalias[i].key == key) break;
    if (i == maptexalias_count) {
        if (maptexalias_count == MAPTEXALIAS_MAX) return 0;
        maptexalias[maptexalias_count++] = (MapTexAlias){key, mine, theirs, other};
    } else {
        maptexalias[i].replaced = theirs;            /* asked again: keep the first original */
    }
    if (mine != theirs) host->write_guest(host->context, values + 4 * (uint32_t)at, &theirs, 4);
    return 1;
}

static void maptexalias_restore(void)
{
    unsigned put = 0;
    for (unsigned i = maptexalias_count; i-- > 0;) {
        uint32_t values = 0, now = 0;
        int at = texture_slot(maptexalias[i].key, &values);
        if (at >= 0 && host->read_guest(host->context, values + 4 * (uint32_t)at, &now, 4) &&
            now == maptexalias[i].replaced) {
            host->write_guest(host->context, values + 4 * (uint32_t)at, &maptexalias[i].original, 4);
            put++;
        }
    }
    if (maptexalias_count) {
        char line[96];
        snprintf(line, sizeof(line), "map textures by name: %u of %u put back", put, maptexalias_count);
        host->log(host->context, line);
    }
    maptexalias_count = 0;
}

/* by-name swaps whose game texture wasn't in the table yet: the PDA loads
 * its portraits when its Deck screen opens, so they're looked for every
 * second and swapped once there */
#define BYNAME_WAIT_MAX 128
typedef struct { uint32_t from, to; } ByNameWait;
static ByNameWait byname_wait[BYNAME_WAIT_MAX];
static unsigned byname_wait_count;

static void byname_wait_poll(void)
{
    unsigned done = 0;
    for (unsigned i = 0; i < byname_wait_count;) {
        if (maptexalias_set(byname_wait[i].from, byname_wait[i].to)) {
            byname_wait[i] = byname_wait[--byname_wait_count];
            done++;
        } else {
            i++;
        }
    }
    if (done) {
        char line[96];
        snprintf(line, sizeof(line), "map HUD art: %u textures drawn by name swapped once the game had them", done);
        host->log(host->context, line);
    }
}

/* DataPod_DrawTexture (0xC5900, the PDA's by-hash draw: card portraits
 * "%s%i_face"): a hash with a map texture of its own draws that one, from the
 * first frame the Deck screen shows. The table swap above only catches the
 * game's portrait once the PDA has loaded it, so for a moment the game's own
 * showed and then flickered to the map's. Only when the map's texture is
 * resident; otherwise the game's draws as before. */
static int byname_target(uint32_t from, uint32_t *to)
{
    for (unsigned i = 0; i < maptexalias_count; i++)
        if (maptexalias[i].key == from) { *to = maptexalias[i].other; return 1; }
    for (unsigned i = 0; i < byname_wait_count; i++)
        if (byname_wait[i].from == from) { *to = byname_wait[i].to; return 1; }
    return 0;
}

static void MERC_PLUGIN_CALL datapod_draw_before(void *ud, MercHookContext *c)
{
    (void)ud;
    uint32_t hash = 0, to = 0, values = 0, tex = 0;
    if ((!maptexalias_count && !byname_wait_count) ||
        !host->read_guest(host->context, c->esp + 4, &hash, 4) || !byname_target(hash, &to)) return;
    int at = texture_slot(to, &values);
    if (at < 0 || !host->read_guest(host->context, values + 4 * (uint32_t)at, &tex, 4) || !tex) return;
    host->write_guest(host->context, c->esp + 4, &to, 4);
}

static void maptexalias_poll(double dt)
{
    if (!maptexalias_count && !byname_wait_count) return;
    if (byname_wait_count) byname_wait_poll();     /* every frame: swapped the frame the game's loads */
    if ((maptexalias_timer += dt) < 1.0) return;
    maptexalias_timer = 0;
    for (unsigned i = 0; i < maptexalias_count; i++) {
        uint32_t values = 0, now = 0;
        int at = texture_slot(maptexalias[i].key, &values);
        if (at >= 0 && host->read_guest(host->context, values + 4 * (uint32_t)at, &now, 4) &&
            now == maptexalias[i].original)
            maptexalias_set(maptexalias[i].key, maptexalias[i].other);
    }
}

/* every frame: swap the sprites of textures now loaded, the frame each one
 * is (gives up after 60 s). 0.278 looked each half second, and every
 * Foundry_MapTexture call counted as another half second for the ones asked
 * for before it, so with the deck's portraits a map's first textures were
 * given up on as the map loaded (bigflags: the PDA's Factions page). */
static double tcount_timer = -1;     /* seconds into the level; the template table's fill logged at 15 */
static int scribble_logged = 1;      /* the script values' use logged with it */

/* The game's script values (scribble memory, 0x414150): every
 * Utility_Write*ToScribbleMemory value (numbers as "%f" text) in one text
 * pool, {name hash, text, NUL} end to end from +0x3008 to +0x300C, the keys'
 * count at +0. Retail's pool is 16 KB and a save kept only 16 KB of it, so a
 * mod's values past that were lost on loading (Gaza, 0.294); the port's
 * pool is 64 KB now and packs a bigger one into the save (about 30 KB of
 * values fit). */
static void scribble_report(char *line, size_t size)
{
    uint32_t keys = 0, base = 0, end = 0;
    const uint32_t g = MF_VAR_g_ScribbleMemory;
    if (!host->read_guest(host->context, g, &keys, 4) ||
        !host->read_guest(host->context, g + MF_OFF_ScribbleMemory_text, &base, 4) ||
        !host->read_guest(host->context, g + MF_OFF_ScribbleMemory_end, &end, 4) || end < base) {
        snprintf(line, size, "script values: unreadable");
        return;
    }
    const uint32_t used = end - base;
    snprintf(line, size, "script values: %u bytes in %u keys%s", used, keys,
             used > 30000u || keys > 1900u ? " (near what a save holds: about 30 KB packed, 2,032 keys)"
             : used > 16384u ? " (past retail's 16 KB: the port packs them into the save)" : "");
}

/* Foundry_CentreWheels(model, ...): vehicles whose wheel model
 * (geometryNameWheel) is one of these draw their wheels at x 0 in the
 * vehicle's frame, so a motorbike's pair of physics wheels draws as one tyre
 * that still spins and steers. The port reads the list at 0x87BF40 (count,
 * then up to 15 name hashes) in 0x45C50 each frame; it stays until replaced
 * (a mod's own wheel models are harmless anywhere). Needs the port build of
 * toolkit 0.296. Returns how many are listed. */
#define CENTRE_WHEELS 0x87BF40u
static void push_number(uint32_t L, double v);
static int MERC_PLUGIN_CALL cmd_centre_wheels(void *ud, uint32_t L)
{
    uint32_t list[16] = {0}, n = 0;
    char name[72], line[160];
    (void)ud;
    for (int i = 1; i <= 15 && lua_arg_text(L, i, name, sizeof(name)) && name[0]; i++)
        list[1 + n++] = hash_name(name);
    list[0] = n;
    host->write_guest(host->context, CENTRE_WHEELS, list, 4u * (1u + n));
    snprintf(line, sizeof(line), "centre wheels: %u wheel model%s drawn on vehicles' centre line", n, n == 1 ? "" : "s");
    host->log(host->context, line);
    push_number(L, (double)n);
    return 1;
}

/* Foundry_ScriptValues(): that line, for the console. */
static int MERC_PLUGIN_CALL cmd_script_values(void *ud, uint32_t L)
{
    char line[200];
    (void)ud;
    scribble_report(line, sizeof(line));
    push_text(L, line);
    return 1;
}
static void maptex_poll(double dt)
{
    maptex_clock += dt;
    if (tcount_timer >= 0 && (tcount_timer += dt) > 15.0) {
        uint32_t n = 0;
        char line[160];
        tcount_timer = -1;
        host->read_guest(host->context, MF_VAR_g_TemplateTable, &n, 4);
        snprintf(line, sizeof(line), "template table: %u of 4096 templates loaded%s", n,
                 n > 3900 ? " (nearly full: templates past the end don't load, and what spawns from them has no "
                            "properties)" : "");
        host->log(host->context, line);
    }
    if (tcount_timer < 0 && !scribble_logged) {
        char line[200];
        scribble_logged = 1;
        scribble_report(line, sizeof(line));
        host->log(host->context, line);
    }
    maptexalias_poll(dt);
    maptemplate_poll(dt);
    any_captive_poll(dt);
    shop_poll(dt);
    if (!maptex_pending_count) return;
    for (unsigned i = 0; i < maptex_pending_count;) {
        MapTexPending *p = &maptex_pending[i];
        char line[160];
        if (texture_loaded(p->to)) {
            unsigned n = maptex_swap(p->from, p->to);
            if (n)
                snprintf(line, sizeof(line), "map HUD art: %08X drawn as %08X on %u sprites (loaded after %.1f s)",
                         p->from, p->to, n, maptex_clock - p->since);
            else {                    /* drawn by name (no sprite): the texture table */
                int now = maptexalias_set(p->from, p->to);
                if (!now && byname_wait_count < BYNAME_WAIT_MAX)
                    byname_wait[byname_wait_count++] = (ByNameWait){p->from, p->to};
                snprintf(line, sizeof(line), "map HUD art: %08X drawn by name as %08X: %s (loaded after %.1f s)",
                         p->from, p->to, now ? "swapped" :
                         "the game's isn't loaded yet; swapped when it is", maptex_clock - p->since);
            }
        } else if (maptex_clock - p->since > 60.0) {
            uint32_t values = 0, tex = 0;
            uint8_t state[2] = {0, 0};
            uint16_t ready = 0;
            int at = texture_slot(p->to, &values);
            if (at >= 0 && host->read_guest(host->context, values + 4 * (uint32_t)at, &tex, 4) && tex) {
                host->read_guest(host->context, tex + 0x20, state, 2);
                host->read_guest(host->context, tex + 0x2C, &ready, 2);
            }
            snprintf(line, sizeof(line), "map HUD art: %08X never loaded (%s; texture %08X, +0x20 %d, +0x21 %d, "
                     "+0x2C %u); the game's %08X stays", p->to,
                     at < 0 ? "not in the texture table" : "in the texture table", tex, (int8_t)state[0], state[1],
                     ready, p->from);
        } else {
            i++;
            continue;
        }
        host->log(host->context, line);
        *p = maptex_pending[--maptex_pending_count];
    }
}

/* Foundry_MapTexture(texture, other): every HUD sprite drawn from texture is
 * drawn from other until the next level loads, once other is loaded (it's
 * kept resident as the game's own HUD textures are). Returns true, or nil. */
static int MERC_PLUGIN_CALL cmd_map_texture(void *ud, uint32_t L)
{
    char from[96], to[96];
    (void)ud;
    if (!lua_arg_text(L, 1, from, sizeof(from)) || !lua_arg_text(L, 2, to, sizeof(to))) return 0;
    uint32_t want = hash_name(from), other = hash_name(to);
    if (!want || !other || maptex_pending_count == MAPTEX_PENDING_MAX) return 0;
    MercGuestCall c;
    uint32_t keep[2] = {other, 0};
    guest(TEXTURE_KEEP, 0, 2, keep, &c);
    /* 0x205D80 only lists it (it's asked for when the category is next
     * refreshed, which may not happen until the next level): ask for it now,
     * as the game's own load-on-demand does (0x10CC60: 0x1F7A70(hash, 0) on
     * 0x7934C8; given back with 0x1F7AF0 on the next level load) */
    unsigned asked = 0;
    while (asked < texreq_count && texreq[asked] != other) asked++;   /* once per texture */
    if (asked == texreq_count && texreq_count < TEXREQ_MAX && guest(TEXTURE_REQUEST, TEXTURE_STREAMER, 2, keep, &c))
        texreq[texreq_count++] = other;
    maptex_pending[maptex_pending_count++] = (MapTexPending){want, other, maptex_clock};
    maptex_poll(0.0);                              /* look right away */
    uint32_t args[2] = {L, 1};
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
}

/* every second while a map's texts are on: a key back on the game's text (its
 * strings rebuilt, as a mission's merge in) gets the map's again */
static double maptext_timer;
static void datapod_remap(void);
static void maptext_poll(double dt)
{
    if (!maptext_count || (maptext_timer += dt) < 1.0) return;
    maptext_timer = 0;
    datapod_remap();                 /* a card list built before the map's texts were on */
    uint32_t table = string_table();
    unsigned again = 0;
    for (unsigned i = 0; table && i < maptext_count; i++) {
        MapTextSwap *m = &maptext[i];
        uint32_t values = 0, ovalues = 0, now = 0, theirs = 0;
        int slot = string_slot(table, m->key, &values);
        int from = string_slot(table, m->other, &ovalues);
        if (slot < 0 || !host->read_guest(host->context, values + 4 * (uint32_t)slot, &now, 4)) continue;
        /* the map's text as it is now (a merge can move it too) */
        if (from >= 0 && host->read_guest(host->context, ovalues + 4 * (uint32_t)from, &theirs, 4) && theirs)
            m->replaced = theirs;
        if (now == m->replaced) continue;
        /* the game's text again: the old pointer, or a new copy of the
         * game's text merged in since (a record loaded again); either way
         * that's what goes back on the next level load */
        m->original = now;
        host->write_guest(host->context, values + 4 * (uint32_t)slot, &m->replaced, 4);
        again++;
    }
    if (again) {
        char line[96];
        snprintf(line, sizeof(line), "map text: %u strings were the game's again; the map's put back", again);
        host->log(host->context, line);
    }
}

/* The PDA's card list (DataPod_BuildCards 0xC9700): 52 entries of 0x28
 * bytes from this+0xDB44 (the rank's word), holding the hashes of
 * cards.<suit>.<n>.Name (+0x10), .charName (+0x14) and .bio (+0x18), which
 * the Deck screen draws by hash. The string swap above changes what those
 * keys hold, but cards whose text the screen had already laid out kept the
 * game's (about half of a suit in the 0.225 playtest: the bios). Each entry
 * whose key has map text is pointed at the map's own key instead, so the
 * screen asks for a string it has never shown; put back on level load. */
#define DATAPOD_CARDS 0xDB44u
#define DATAPOD_CARD_SIZE 0x28u
#define DATAPOD_CARD_COUNT 52u
typedef struct { uint32_t addr, original, replaced; } DataPodSwap;
static DataPodSwap dpswap[DATAPOD_CARD_COUNT * 3];
static unsigned dpswap_count;
static uint32_t datapod_vm, dpswap_vm;       /* the PDA block the swaps were made in */
#define DATAPOD_VM_PTR (0x0034ABC8u + 0x24u)  /* the current PDA block (RsDataPodVM's +0x24) */

static void datapod_remap(void)
{
    if (!datapod_vm || !maptext_count) return;
    unsigned n = 0;
    for (uint32_t i = 0; i < DATAPOD_CARD_COUNT; i++) {
        for (uint32_t off = 0x10; off <= 0x18; off += 4) {
            uint32_t addr = datapod_vm + DATAPOD_CARDS + i * DATAPOD_CARD_SIZE + off, h = 0;
            if (!host->read_guest(host->context, addr, &h, 4) || !h) continue;
            for (unsigned j = 0; j < maptext_count; j++) {
                if (maptext[j].key != h) continue;
                uint32_t to = maptext[j].other;
                if (!host->write_guest(host->context, addr, &to, 4)) break;
                unsigned k;
                for (k = 0; k < dpswap_count && dpswap[k].addr != addr; k++) {}
                if (k == dpswap_count && dpswap_count < DATAPOD_CARD_COUNT * 3) {
                    dpswap[dpswap_count++] = (DataPodSwap){addr, h, to};
                    dpswap_vm = datapod_vm;
                }
                else if (k < dpswap_count)
                    dpswap[k].replaced = to;
                n++;
                break;
            }
        }
    }
    (void)n;
}

/* undone only in the PDA block they were made in, if it's still the game's:
 * Level_Load builds the PDA again (0xCE460 allocates), so after a level
 * change the old block's addresses may be memory the new level has taken
 * (as the HUD art's were, 0.310) */
static void datapod_restore(void)
{
    uint32_t cur = 0;
    if (dpswap_count && (!host->read_guest(host->context, DATAPOD_VM_PTR, &cur, 4) || cur != dpswap_vm)) {
        char line[120];
        snprintf(line, sizeof(line), "map text: %u PDA card swaps left alone (their PDA block %08X is gone, now %08X)",
                 dpswap_count, dpswap_vm, cur);
        host->log(host->context, line);
        dpswap_count = 0;
    }
    for (unsigned i = 0; i < dpswap_count; i++) {
        uint32_t now = 0;
        if (host->read_guest(host->context, dpswap[i].addr, &now, 4) && now == dpswap[i].replaced)
            host->write_guest(host->context, dpswap[i].addr, &dpswap[i].original, 4);
    }
    dpswap_count = 0;
    datapod_vm = 0;
}

static void MERC_PLUGIN_CALL datapod_cards_before(void *ud, MercHookContext *c)
{
    (void)ud;
    datapod_vm = c->ecx;
}

static void MERC_PLUGIN_CALL datapod_cards_after(void *ud, MercHookContext *c)
{
    (void)ud; (void)c;
    datapod_remap();
}

/* Retail's pause menu (0xD7060) offers SAVE GAME and LOAD GAME only while
 * scribble memory has no mission_accepted (CANCEL CONTRACT while it has).
 * Logged each time the menu is built, with the contract a mod started, so a
 * log shows when the menu was made and what it saw. */
static void MERC_PLUGIN_CALL pause_menu_before(void *ud, MercHookContext *c)
{
    (void)ud; (void)c;
    MercGuestCall g;
    uint32_t key = hash_name("mission_accepted");
    int accepted = guest(MF_FN_ScribbleMemory_GetString, MF_VAR_g_ScribbleMemory, 1, &key, &g) && g.eax;
    key = hash_name("foundry_active_contract");
    char contract[64] = "";
    if (guest(MF_FN_ScribbleMemory_GetString, MF_VAR_g_ScribbleMemory, 1, &key, &g) && g.eax)
        read_text(g.eax, contract, sizeof(contract));
    char msg[160];
    snprintf(msg, sizeof(msg), "pause menu built: %s (mission_accepted %s; foundry contract %s)",
             accepted ? "CANCEL CONTRACT, no saving" : "SAVE GAME offered",
             accepted ? "set" : "unset", contract[0] ? contract : "none");
    host->log(host->context, msg);
}

/* Retail's HQ briefing (Briefing_Init 0x175B10, Briefing_Deinit 0x1758D0)
 * sets the player up with the player as a human (0x8ED00, vtable +0x2C of
 * the object named player0): 0x5CA70(1) puts the weapon away (0x5BF30 with
 * no weapon), virtual +0x290(0), and +0x817 (the human's run permission,
 * which the physics body's +0x65 follows) cleared, so the player walks;
 * then control mode 13 (0xA3F80: move, look and use only). Deinit undoes
 * them: 0x5C950(1) takes out the last weapon (+0xA8C), +0x290(1), +0x817
 * set. The control mode is the script's (Player_EnableControl). */
#define FIND_HUMAN 0x8ED00u
#define HUMAN_HOLSTER 0x5CA70u
#define HUMAN_DRAW 0x5C950u
#define HUMAN_VT_BRIEFING 0x290u
#define HUMAN_CAN_RUN 0x817u
static int hq_mode_on;

static int hq_mode_set(int on)
{
    MercGuestCall c;
    uint32_t hash = PLAYER0_HASH, human = 0, vt = 0, fn = 0, one = 1, flag = on ? 0u : 1u;
    uint8_t run = on ? 0 : 1;
    if (!guest(FIND_HUMAN, 0, 1, &hash, &c) || !(human = c.eax)) return 0;
    if (!guest(on ? HUMAN_HOLSTER : HUMAN_DRAW, human, 1, &one, &c)) return 0;
    if (host->read_guest(host->context, human, &vt, 4) && vt &&
        host->read_guest(host->context, vt + HUMAN_VT_BRIEFING, &fn, 4) && fn)
        guest(fn, human, 1, &flag, &c);
    host->write_guest(host->context, human + HUMAN_CAN_RUN, &run, 1);
    hq_mode_on = on;
    return 1;
}

/* Foundry_HqMode(on): the player as in retail's HQs (weapon away, walking)
 * with true, back to normal with false/nil. Returns true, or nil with no
 * player. */
static int MERC_PLUGIN_CALL cmd_hq_mode(void *ud, uint32_t L)
{
    char text[16];
    uint32_t args[2] = {L, 1};
    int on;
    (void)ud;
    if (lua_arg_text(L, 1, text, sizeof(text)))
        on = !(!text[0] || !strcmp(text, "0") || !_stricmp(text, "false") || !_stricmp(text, "off"));
    else
        on = (int)lua_call(MF_FN_lua_gettop, 1, args) >= 1 && lua_call(MF_FN_Lua_ArgBool, 2, args) != 0;
    if (on == hq_mode_on) {
        lua_call(MF_FN_Lua_PushBool, 2, args);
        return 1;
    }
    if (!hq_mode_set(on)) {
        host->log(host->context, "hq mode: no player to set");
        return 0;
    }
    host->log(host->context, on ? "hq mode: weapon away, walking (as retail's HQs)" : "hq mode: back to normal");
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
}

static unsigned maptemplate_count, maptemplate_used;    /* Foundry_MapTemplate's, below */
static void maptemplate_restore(void);
static int any_captive_on;
static void any_captive_set(int on);
static void shop_url_restore(void);
static double any_captive_wait;
static void MERC_PLUGIN_CALL level_load_before(void *ud, MercHookContext *c)
{
    (void)ud; (void)c;
    if (maptemplate_count) {
        char line[96];
        snprintf(line, sizeof(line), "map templates: %u swaps forgotten (%u spawns used one)", maptemplate_count,
                 maptemplate_used);
        host->log(host->context, line);
    }
    maptemplate_restore();
    shop_level_reset();
    shop_url_restore();
    tcount_timer = 0;
    scribble_logged = 0;
    pool_check("as the level unloads");   /* before the unload walks the lists */
    pool_level_time = 0;
    flyer_count = 0;                           /* the map's script sets its loops again */
    memset(card_seen, 0, sizeof(card_seen));   /* handles are reused on the next level */
    /* so are handles and object addresses together: a level loaded after
     * another kept the first level's models for them (Gaza's pagers, never
     * found by Foundry_FindPlaced after SW, 0.297) */
    memset(model_cache, 0, sizeof(model_cache));
    if (any_captive_on || any_captive_wait >= 0) any_captive_set(0);
    { uint8_t z = 0; host->write_guest(host->context, 0x87BF00u, &z, 1); }   /* fines back (Foundry_MapFines) */
    { uint8_t z = 0; host->write_guest(host->context, 0x87BF01u, &z, 1); }   /* the store's mood check back (Foundry_MapShopOpen) */
    { uint32_t z = 0; host->write_guest(host->context, 0x87B000u, &z, 4); }   /* every AI takes grudges again (Foundry_IgnoreAttacks) */
    maptemplate_count = maptemplate_used = 0;
    datapod_restore();
    maptext_restore();
    mapsprite_restore();
    maptexalias_restore();
    byname_wait_count = 0;
    texreq_release();
    maptex_pending_count = 0;
    hq_mode_on = 0;               /* a new level has a new player */
}

/* Foundry_MapTemplate(template, other): until the next level loads, the game
 * spawning template (by name: its support code, scripts, the shop) spawns
 * it with other's properties. For a new map's own versions of the extraction
 * helicopter, support aircraft and deliveries (maps json
 * "template_overrides"). Returns true.
 * In place: template's entry in g_TemplateTable (its property block) points
 * at other's block, so the spawn keeps template's name and hash. Swapping the
 * spawn's hash for other's instead (until 0.251) spawned the copy under its
 * own name, and the extraction Black Hawk spawned that way hovered 5 m up
 * instead of landing (the game's support code knows it by its template).
 * The block is swapped at the first spawn of template once other's layer has
 * loaded (the map's script loads it after ScriptInit's call), and put back
 * before the next level loads. When other isn't in the table, the spawn's
 * hash is swapped as before. */
#define MAPTEMPLATE_MAX 64
typedef struct { uint32_t from, to, slot, original, mine; int placed; } MapTemplateSwap;
static MapTemplateSwap maptemplate[MAPTEMPLATE_MAX];
static unsigned maptemplate_count, maptemplate_used;

/* g_TemplateTable: +8 0x1000 property block pointers, +0x4008 their hashes
 * (linear probing from hash & 0xFFF, 0 = empty, as HashTable_Find). The
 * slot of hash's first entry (what HashTable_Find finds), or -1. */
static int template_slot(uint32_t hash)
{
    uint32_t k = 0;
    if (!hash) return -1;
    for (uint32_t n = 0, i = hash & 0xFFF; n < 0x1000; n++, i = (i + 1) & 0xFFF) {
        if (!host->read_guest(host->context, MF_VAR_g_TemplateTable + 0x4008 + 4 * i, &k, 4) || !k) return -1;
        if (k == hash) return (int)i;
    }
    return -1;
}

/* 1 when swap's template now spawns with other's block */
static int maptemplate_place(MapTemplateSwap *s)
{
    if (s->placed) {
        uint32_t now = 0;
        if (host->read_guest(host->context, MF_VAR_g_TemplateTable + 8 + 4 * s->slot, &now, 4) && now == s->mine)
            return 1;
        s->placed = 0;                       /* the table changed under us: look again */
    }
    int at = template_slot(s->from), from = template_slot(s->to);
    uint32_t original = 0, mine = 0;
    if (at < 0 || from < 0 || at == from ||
        !host->read_guest(host->context, MF_VAR_g_TemplateTable + 8 + 4 * (uint32_t)at, &original, 4) ||
        !host->read_guest(host->context, MF_VAR_g_TemplateTable + 8 + 4 * (uint32_t)from, &mine, 4) || !mine)
        return 0;
    if (original != mine) host->write_guest(host->context, MF_VAR_g_TemplateTable + 8 + 4 * (uint32_t)at, &mine, 4);
    s->slot = (uint32_t)at;
    s->original = original;
    s->mine = mine;
    s->placed = 1;
    char line[96];
    snprintf(line, sizeof(line), "map templates: %08x spawns with %08x's properties, in place", s->from, s->to);
    host->log(host->context, line);
    return 1;
}

/* each second: swaps not placed yet (the copy's layer not loaded at the
 * call), so whatever reads the table, not only the spawner, gets the copy */
static double maptemplate_timer;
static void maptemplate_poll(double dt)
{
    if (!maptemplate_count || (maptemplate_timer += dt) < 1.0) return;
    maptemplate_timer = 0;
    for (unsigned i = 0; i < maptemplate_count; i++)
        if (!maptemplate[i].placed) maptemplate_place(&maptemplate[i]);
}

static void maptemplate_restore(void)
{
    for (unsigned i = maptemplate_count; i-- > 0;) {
        MapTemplateSwap *s = &maptemplate[i];
        uint32_t now = 0;
        if (s->placed && host->read_guest(host->context, MF_VAR_g_TemplateTable + 8 + 4 * s->slot, &now, 4) &&
            now == s->mine)
            host->write_guest(host->context, MF_VAR_g_TemplateTable + 8 + 4 * s->slot, &s->original, 4);
        s->placed = 0;
    }
}

static void MERC_PLUGIN_CALL spawn_before(void *ud, MercHookContext *c)
{
    (void)ud;
    uint32_t hash = 0;
    if (!maptemplate_count || !host->read_guest(host->context, c->esp + 4, &hash, 4)) return;
    for (unsigned i = 0; i < maptemplate_count; i++)
        if (maptemplate[i].from == hash) {
            if (!maptemplate_place(&maptemplate[i]))
                host->write_guest(host->context, c->esp + 4, &maptemplate[i].to, 4);
            maptemplate_used++;
            return;
        }
}

/* Foundry_MapCaptives(on): until the next level loads, every subdued human
 * counts as a captive the way a verifiable target does. The game has a
 * switch for it: the byte at 0x32079C, read beside each human's
 * IsVerifiable (+0x808) at 15 places (0x3EC60 is IsVerifiable || it, the
 * carry, drop and seat checks among them) and written nowhere: a debug
 * setting with no menu left. Cleared before the next level loads. Returns
 * true. For a new map's "any_captive".
 * Turned on 15 s after the call (Foundry_MapCaptives(true, 0) at once), not
 * during the load: 0.254 set it in ScriptInit and Gaza crashed drawing its
 * first frame (0x28B180), and the log line when it goes on tells whether a
 * crash follows it. */
#define ANY_CAPTIVE_SWITCH 0x32079Cu
#define ANY_CAPTIVE_DELAY 15.0
static double any_captive_wait = -1;     /* seconds until it goes on; < 0 none pending */
static void any_captive_set(int on)
{
    uint8_t b = on ? 1 : 0;
    host->write_guest(host->context, ANY_CAPTIVE_SWITCH, &b, 1);
    any_captive_on = on;
    if (!on) any_captive_wait = -1;
}

static void any_captive_poll(double dt)
{
    if (any_captive_wait < 0 || (any_captive_wait -= dt) > 0) return;
    any_captive_wait = -1;
    any_captive_set(1);
    host->log(host->context, "map captives: on now (any subdued human counts as a captive until the next level loads)");
}

/* Foundry_MapFines(on): false turns off the fine for a death the player
 * caused (0x56FD0: $100 off the player's money) until the next level loads,
 * through a byte the port reads there (0x87BF00, free guest memory after the
 * moved mission table; Patch-Generated "No fines switch"). Faction moods are
 * another path and stay. Returns true. For a new map's "collateral_fines". */
static int MERC_PLUGIN_CALL cmd_map_fines(void *ud, uint32_t L)
{
    (void)ud;
    uint32_t args[2] = {L, 1};
    int on = (int)lua_call(MF_FN_lua_gettop, 1, args) < 1 || lua_call(MF_FN_Lua_ArgBool, 2, args) != 0;
    uint8_t b = on ? 0 : 1;
    host->write_guest(host->context, 0x87BF00u, &b, 1);
    host->log(host->context, on ? "map fines: the game's (a death the player caused costs money)"
                                : "map fines: off until the next level loads");
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
}

/* Objects scripts can't name (a placed charge, AmmoPlaced, has no name):
 * Foundry_FindPlaced(model, n) gives the n-th live object (1 unless given)
 * drawing that model (its GeometryFile) as handle, x, y, z, or nothing;
 * Foundry_MoveObject(handle, x, y, z, yaw?) puts it there (degrees, as
 * the editor's live moves). For a map script's pager bombs: one picked up
 * is moved along with whoever carries it, and the C4 detonator's broadcast
 * sets it off where it is. */
static void push_number(uint32_t L, double v)
{
    uint32_t w[3] = {L, 0, 0};
    memcpy(&w[1], &v, 8);
    lua_call(MF_FN_lua_pushnumber, 3, w);
}

/* 0.333: the key Foundry_FindPlaced gives a script, and the commands take:
 * the game's Lua numbers are single floats (24 bits), and a handle such as
 * 496650665 came back rounded, so nothing matched it again (Gaza's pager
 * rounds were new on every look, ProjectilePos answered nothing). The key is
 * the object's slot (14 bits) and the low 10 bits of its handle, under 2^24:
 * exact in a float, and a slot reused by another object doesn't match. */
static double placed_key(unsigned slot, uint32_t handle)
{
    return (double)(slot + OBJECT_SLOTS * (handle & 0x3FFu));
}

static uint32_t find_slot_object(uint32_t handle)
{
    static uint32_t objs[OBJECT_SLOTS], handles[OBJECT_SLOTS];
    if (handle && handle < OBJECT_SLOTS * 0x400u) {
        const uint32_t slot = handle % OBJECT_SLOTS, gen = handle / OBJECT_SLOTS;
        uint32_t o = 0, h = 0;
        if (!host->read_guest(host->context, OBJECTS + 4u * slot, &o, 4) ||
            !host->read_guest(host->context, OBJECT_HANDLES + 4u * slot, &h, 4)) return 0;
        return o && (h & 0x3FFu) == gen ? o : 0;
    }
    if (!handle || !host->read_guest(host->context, OBJECTS, objs, sizeof(objs)) ||
        !host->read_guest(host->context, OBJECT_HANDLES, handles, sizeof(handles))) return 0;
    for (unsigned i = 0; i < OBJECT_SLOTS; i++)
        if (objs[i] && handles[i] == handle) return objs[i];
    return 0;
}

static int MERC_PLUGIN_CALL cmd_find_placed(void *ud, uint32_t L)
{
    (void)ud;
    char model[64];
    if (!lua_arg_text(L, 1, model, sizeof(model))) return 0;
    int want = 1;
    uint32_t a2[2] = {L, 2};
    MercGuestCall c;
    if ((int)lua_call(MF_FN_lua_gettop, 1, a2) >= 2 && guest(MF_FN_Lua_ArgNumber, 0, 2, a2, &c) && c.st0 >= 1)
        want = (int)c.st0;
    static uint32_t objs[OBJECT_SLOTS], handles[OBJECT_SLOTS];
    if (!host->read_guest(host->context, OBJECTS, objs, sizeof(objs)) ||
        !host->read_guest(host->context, OBJECT_HANDLES, handles, sizeof(handles))) return 0;
    /* within 150 m of the player only: each object's model is a guest call
     * the first time (cached by handle), and the table has 16,384 slots */
    uint32_t player = find_player();
    float pm[16];
    if (!player || !host->read_guest(host->context, player + MF_OFF_Actor_transform, pm, sizeof(pm))) return 0;
    int seen = 0;
    for (unsigned i = 0; i < OBJECT_SLOTS; i++) {
        uint32_t obj = objs[i];
        unsigned char head[0x34];
        if (!obj || !host->read_guest(host->context, obj, head, sizeof(head))) continue;
        uint16_t flags;
        memcpy(&flags, head + MF_OFF_WorldObject_flags, 2);
        if (!(flags & 1) || (flags & 4)) continue;          /* active, not removed */
        float q[3];
        memcpy(q, head + WORLDOBJECT_POSITION, 12);
        float dx = q[0] - pm[12], dz = q[2] - pm[14];
        if (dx * dx + dz * dz > 150.0f * 150.0f) continue;
        char m[48];
        object_model(obj, handles[i], m, sizeof(m));
        if (_stricmp(m, model) != 0 || ++seen < want) continue;
        float pos[3];
        memcpy(pos, head + WORLDOBJECT_POSITION, 12);
        push_number(L, placed_key(i, handles[i]));   /* 0.333: a key a float holds */
        for (int k = 0; k < 3; k++) push_number(L, pos[k]);
        return 4;
    }
    return 0;
}

/* Foundry_ProjectilePos(handle): where a thrown or fired round is, as its
 * flight keeps it (0.324). Foundry_FindPlaced gives the world object's
 * position (+0x08), which may stay at the release point while the round
 * flies; the projectile (the object's actor) moves its own: 0x212E0, its
 * detonation, reads +0x2B4 when its +0x2F8 has bit 8 and +0x268 otherwise,
 * and 0x1A490 steps +0x2A8 -> +0x2B4. Gives x, y, z and that flag (1 in
 * flight's position, 0 the other; +2B4 too while +268 is still zero), then
 * +2B4's x, y, z (0.332), or nothing (no such object, no actor, or
 * a position that isn't a number or is over 2 km from the object's). The
 * first answer for each of 8 handles is logged with both positions. From the
 * game's code, not yet seen in game: check its log against FindPlaced's. */
/* An actor's template name hash, as retail's serialization finds it (Actor
 * +8 -> its property record +4 -> sorted inherited property tables, key
 * 0x8D39BDE6 the Name; the port's mod_extensions.c reads it the same way). */
static uint32_t actor_template_hash(uint32_t actor)
{
    uint32_t record = 0, props = 0;
    if (!host->read_guest(host->context, actor + 8u, &record, 4) || !record ||
        !host->read_guest(host->context, record + 4u, &props, 4)) return 0;
    for (unsigned depth = 0; props && depth < 64u; depth++) {
        int16_t count = 0;
        uint32_t entries = 0;
        if (!host->read_guest(host->context, props + 4u, &count, 2) ||
            !host->read_guest(host->context, props + 8u, &entries, 4)) return 0;
        int lo = 0, hi = count - 1;
        while (lo <= hi) {
            const int mid = lo + (hi - lo) / 2;
            uint32_t kv[2];
            if (!host->read_guest(host->context, entries + 8u * (uint32_t)mid, kv, 8)) return 0;
            if (kv[0] == 0x8D39BDE6u) return kv[1];
            if (kv[0] < 0x8D39BDE6u) lo = mid + 1; else hi = mid - 1;
        }
        if (!host->read_guest(host->context, props, &props, 4)) return 0;
    }
    return 0;
}

/* Foundry_FindRounds(ammo template, n): the n-th live round (1 unless
 * given) of that ammunition template, as key, x, y, z, or nothing (0.334).
 * Gaza's thrown pager: Foundry_FindPlaced by model found a round's world
 * object for one look only (it goes inactive in flight) and could also
 * find real frags and pickups. This walks the object table whatever the
 * active flag (not removed ones), takes objects whose actor's template is
 * the ammunition template, and gives the round's flight position (as
 * Foundry_ProjectilePos: +2B4 in flight or while +268 is zero, else +268).
 * The key works with ProjectilePos, MoveObject and RemoveObject. */
static int MERC_PLUGIN_CALL cmd_find_rounds(void *ud, uint32_t L)
{
    (void)ud;
    char ammo[72];
    if (!lua_arg_text(L, 1, ammo, sizeof(ammo))) return 0;
    const uint32_t want = hash_name(ammo);
    int nth = 1;
    uint32_t a2[2] = {L, 2};
    MercGuestCall c;
    if ((int)lua_call(MF_FN_lua_gettop, 1, a2) >= 2 && guest(MF_FN_Lua_ArgNumber, 0, 2, a2, &c) && c.st0 >= 1)
        nth = (int)c.st0;
    static uint32_t objs[OBJECT_SLOTS], handles[OBJECT_SLOTS];
    if (!host->read_guest(host->context, OBJECTS, objs, sizeof(objs)) ||
        !host->read_guest(host->context, OBJECT_HANDLES, handles, sizeof(handles))) return 0;
    int seen = 0;
    uint32_t actors[32];
    unsigned nactors = 0;
    for (unsigned i = 0; i < OBJECT_SLOTS; i++) {
        uint32_t obj = objs[i], actor = 0;
        uint16_t flags = 0;
        if (!obj || !host->read_guest(host->context, obj + MF_OFF_WorldObject_flags, &flags, 2) || (flags & 4) ||
            !host->read_guest(host->context, obj + MF_OFF_WorldObject_actor, &actor, 4) || !actor ||
            actor_template_hash(actor) != want) continue;
        /* 0.335: two world objects can share one round's actor (0.334: keys
         * 2562 and 14669695, one actor): each actor counts once */
        unsigned dup = 0;
        while (dup < nactors && actors[dup] != actor) dup++;
        if (dup < nactors) continue;
        if (nactors < 32u) actors[nactors++] = actor;
        if (++seen < nth) continue;
        float a[3] = {0}, b[3] = {0}, wp[3] = {0};
        uint8_t flag = 0;
        host->read_guest(host->context, actor + 0x2B4u, a, 12);
        host->read_guest(host->context, actor + 0x268u, b, 12);
        host->read_guest(host->context, actor + 0x2F8u, &flag, 1);
        host->read_guest(host->context, obj + WORLDOBJECT_POSITION, wp, 12);
        const int b_zero = b[0] == 0.0f && b[1] == 0.0f && b[2] == 0.0f;
        const float *pp = (flag & 8) || b_zero ? a : b;
        /* 0.335: a flight position that isn't a number or is over 5 km from the
         * object's (0.334: 7.6e28) gives the object's own instead */
        for (int k = 0; k < 3; k++)
            if (!(pp[k] == pp[k]) || fabsf(pp[k] - wp[k]) > 5000.0f) { pp = wp; break; }
        push_number(L, placed_key(i, handles[i]));
        for (int k = 0; k < 3; k++) push_number(L, pp[k]);
        static unsigned said;
        if (said < 12u) {
            said++;
            char line[220];
            snprintf(line, sizeof(line), "find rounds: %s %d: key %.0f, actor %08X, flags %04X, at (%.2f, %.2f, %.2f), the object's +08 (%.2f, %.2f, %.2f)",
                     ammo, seen, placed_key(i, handles[i]), actor, flags, pp[0], pp[1], pp[2], wp[0], wp[1], wp[2]);
            host->log(host->context, line);
        }
        return 4;
    }
    return 0;
}

static int MERC_PLUGIN_CALL cmd_projectile_pos(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    uint32_t args[2] = {L, 1};
    if ((int)lua_call(MF_FN_lua_gettop, 1, args) < 1 || !guest(MF_FN_Lua_ArgNumber, 0, 2, args, &c)) return 0;
    const uint32_t handle = (uint32_t)c.st0;
    uint32_t obj = find_slot_object(handle), actor = 0;
    if (!obj || !host->read_guest(host->context, obj + MF_OFF_WorldObject_actor, &actor, 4) || !actor) return 0;
    float wp[3], a[3], b[3];
    uint8_t flag = 0;
    if (!host->read_guest(host->context, obj + WORLDOBJECT_POSITION, wp, 12) ||
        !host->read_guest(host->context, actor + 0x2B4u, a, 12) || !host->read_guest(host->context, actor + 0x268u, b, 12) ||
        !host->read_guest(host->context, actor + 0x2F8u, &flag, 1)) return 0;
    const int flying = (flag & 8) != 0;
    /* 0.332: +268 is zero on a round's first frames (Gaza's pager, flag 00):
     * then +2B4, which is already away from the release point */
    const int b_zero = b[0] == 0.0f && b[1] == 0.0f && b[2] == 0.0f;
    const float *p = flying || b_zero ? a : b;
    static uint32_t said[8];
    int known = 0;
    for (unsigned i = 0; i < 8 && !known; i++) known = said[i] == handle;
    if (!known) {
        for (unsigned i = 0; i < 8; i++) if (!said[i]) { said[i] = handle; break; }
        char line[240];
        snprintf(line, sizeof(line), "projectile pos: %u (actor %08X, flag %02X): +2B4 (%.2f, %.2f, %.2f), +268 (%.2f, %.2f, %.2f), "
                 "the object's +08 (%.2f, %.2f, %.2f)", handle, actor, flag, a[0], a[1], a[2], b[0], b[1], b[2],
                 wp[0], wp[1], wp[2]);
        host->log(host->context, line);
    }
    for (int k = 0; k < 3; k++)
        if (!(p[k] == p[k]) || fabsf(p[k] - wp[k]) > 2000.0f) return 0;
    for (int k = 0; k < 3; k++) push_number(L, p[k]);
    push_number(L, flying ? 1.0 : 0.0);
    for (int k = 0; k < 3; k++) push_number(L, a[k]);   /* 0.332: +2B4 as well */
    return 7;
}

/* Foundry_IgnoreAttacks(template, ...): AIs spawned from these templates (a
 * vehicle's pilot, a person) take no grudge against whoever shoots at them
 * until the next level loads: the port skips Ai_AddEnemy for them (0x65250;
 * the list, count + 15 name hashes, at 0x87B000). For Gaza's traffic drones,
 * which a shot pulled off their loops (0.339): name the pilot's template,
 * whose AI flies the drone. Returns how many are listed. */
static int MERC_PLUGIN_CALL cmd_ignore_attacks(void *ud, uint32_t L)
{
    (void)ud;
    uint32_t a[2] = {L, 1};
    int n = (int)lua_call(MF_FN_lua_gettop, 1, a);
    uint32_t list[16] = {0};
    for (int i = 1; i <= n && list[0] < 15u; i++) {
        char t[72];
        if (lua_arg_text(L, i, t, sizeof(t)) && t[0]) list[1 + list[0]++] = hash_name(t);
    }
    host->write_guest(host->context, 0x87B000u, list, sizeof(list));
    char line[96];
    snprintf(line, sizeof(line), "ignore attacks: %u template(s) take no grudge until the next level loads", list[0]);
    host->log(host->context, line);
    push_number(L, (double)list[0]);
    return 1;
}

/* Foundry_AiAlert(actor): the actor's AI alert level, as the AI keeps it:
 * 0 normal, 1 caution, 2 danger (fighting). The AI is the actor's vtable
 * +0xBC (Lua_ArgAi, as the Ai_ commands find it); its +0x20 indexes its three
 * perception values (+0x4D4 ai_perceptionNormal, +0x4D8 Caution, +0x4DC
 * Danger: 0x6BA70 sets them, 0x653E0 reads by the level). Nil for something
 * with no AI. For a script that only wants calm people (Gaza's pagers). */
static int MERC_PLUGIN_CALL cmd_ai_alert(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    uint32_t args[2] = {L, 1};
    if ((int)lua_call(MF_FN_lua_gettop, 1, args) < 1 || !guest(MF_FN_Lua_ArgAi, 0, 2, args, &c) || !c.eax) return 0;
    uint32_t level = 0xFFFFFFFFu;
    if (!host->read_guest(host->context, c.eax + 0x20, &level, 4) || level > 2) return 0;
    push_number(L, (double)level);
    return 1;
}

/* Foundry_IsEngaging(actor): true when its AI is on alert or fighting
 * (Foundry_AiAlert 1 or 2), false when calm, nil with no AI. */
static int MERC_PLUGIN_CALL cmd_is_engaging(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    uint32_t args[2] = {L, 1};
    if ((int)lua_call(MF_FN_lua_gettop, 1, args) < 1 || !guest(MF_FN_Lua_ArgAi, 0, 2, args, &c) || !c.eax) return 0;
    uint32_t level = 0xFFFFFFFFu;
    if (!host->read_guest(host->context, c.eax + 0x20, &level, 4) || level > 2) return 0;
    uint32_t b[2] = {L, level >= 1 ? 1u : 0u};
    lua_call(MF_FN_Lua_PushBool, 2, b);
    return 1;
}

static int MERC_PLUGIN_CALL cmd_move_object(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    float v[5] = {0};
    uint32_t top[2] = {L, 1};
    int n = (int)lua_call(MF_FN_lua_gettop, 1, top);
    if (n < 4) return 0;
    for (int k = 0; k < 5 && k < n; k++) {
        uint32_t a[2] = {L, (uint32_t)(k + 1)};
        if (!guest(MF_FN_Lua_ArgNumber, 0, 2, a, &c)) return 0;
        v[k] = (float)c.st0;
    }
    uint32_t obj = find_slot_object((uint32_t)v[0]);
    int ok = obj && live_apply(obj, v + 1, v[4] * 3.14159265f / 180.0f, 0);
    uint32_t args[2] = {L, (uint32_t)ok};
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
}

/* Foundry_FlyLoop(name, speed, x1, y1, z1, x2, y2, z2, ...): a named
 * piece of scenery (a plain world object: no actor, no AI, no physics)
 * moved along a closed loop of points every frame at `speed` m/s, facing
 * where it goes (a point 40 m ahead, so it turns smoothly at the corners).
 * For Gaza's drones (0.309): as helicopters they never flew and, placed in
 * the air, went with the level-load stops; as scenery they're only drawn.
 * The loop starts at the point nearest the object. Again with the same name
 * replaces its loop; speed 0 stops it. Up to 16 at once, 64 points each;
 * forgotten as a level loads (the map's script sets them again). True if
 * the loop is taken; the object is looked for until it exists. */
#define FLY_MAX 16
#define FLY_POINTS 64
typedef struct {
    char name[48];
    uint32_t obj, handle;
    int n, logged;
    float pts[3 * FLY_POINTS], len[FLY_POINTS], total, at, speed;
    double look;                 /* seconds until the object is looked for (or checked) again */
    double report;               /* 0.333: seconds until its next status line */
    unsigned finds, reports;
} Flyer;
static Flyer flyers[FLY_MAX];
static unsigned flyer_count;

static void fly_point(const Flyer *f, float s, float *out)
{
    while (s < 0) s += f->total;
    while (s >= f->total) s -= f->total;
    int i = 0;
    while (i < f->n - 1 && s >= f->len[i]) { s -= f->len[i]; i++; }
    const float *a = f->pts + 3 * i, *b = f->pts + 3 * ((i + 1) % f->n);
    float t = f->len[i] > 0 ? s / f->len[i] : 0;
    for (int k = 0; k < 3; k++) out[k] = a[k] + (b[k] - a[k]) * t;
}

static void fly_update(double dt)
{
    for (unsigned i = 0; i < flyer_count; i++) {
        Flyer *f = &flyers[i];
        if (f->speed <= 0 || f->total <= 0) continue;
        f->look -= dt;
        if (f->look <= 0) {
            /* found yet, and still the same object (its handle) */
            if (!f->obj) {
                f->obj = live_find(f->name, "-", 0, NULL);
                if (f->obj) {
                    f->finds++;
                    /* 0.331: the handle the name table gave (World_ObjectFromHandle's
                     * kind), not the object's own +handle field: 0.310 kept that one,
                     * the per-frame check below never matched it, and each drone
                     * was dropped, found again and put back at its nearest point
                     * every frame (Gaza's drones hung in the air) */
                    MercGuestCall hc;
                    const uint32_t want = hash_name(f->name);
                    f->handle = 0;
                    for (unsigned j = 0; j < NAME_SLOTS && !f->handle; j++)
                        if (name_keys[j] == want && guest(MF_FN_World_ObjectFromHandle, 0, 1, &name_handles[j], &hc) &&
                            hc.eax == f->obj)
                            f->handle = name_handles[j];
                    if (!f->handle) { f->obj = 0; f->look = 1.0; continue; }
                    if (f->finds == 1 && i == 0) {
                        /* 0.334: the object moves (+08 follows the loop) but isn't
                         * drawn there: look, read-only, for the matrices that hold
                         * its place now (translation = its position), in its own
                         * block and two steps out, so a drawn one can be moved too */
                        float p0[3];
                        host->read_guest(host->context, f->obj + WORLDOBJECT_POSITION, p0, 12);
                        static uint32_t a1[0x400 / 4], b1[0x400 / 4], c1[0x400 / 4];
                        unsigned found = 0, reads = 0;
                        char line[200];
                        if (host->read_guest(host->context, f->obj, a1, sizeof(a1))) {
                            for (unsigned x = 0; x < 0x400 / 4 && found < 16; x++) {
                                if (x + 3 <= 0x400 / 4 && x * 4 != WORLDOBJECT_POSITION) {
                                    const float *q = (const float *)&a1[x];
                                    if (fabsf(q[0] - p0[0]) < 0.01f && fabsf(q[1] - p0[1]) < 0.01f && fabsf(q[2] - p0[2]) < 0.01f) {
                                        snprintf(line, sizeof(line), "fly loop probe: %s position at obj+%X", f->name, x * 4);
                                        host->log(host->context, line); found++;
                                    }
                                }
                                const uint32_t p1 = a1[x];
                                if (p1 < 0x10000u || p1 >= 0x04000000u || (p1 & 3u) || p1 == f->obj || reads >= 600u ||
                                    !host->read_guest(host->context, p1, b1, sizeof(b1))) continue;
                                reads++;
                                for (unsigned y = 0; y + 3 <= 0x400 / 4 && found < 16; y++) {
                                    const float *q = (const float *)&b1[y];
                                    if (fabsf(q[0] - p0[0]) < 0.01f && fabsf(q[1] - p0[1]) < 0.01f && fabsf(q[2] - p0[2]) < 0.01f) {
                                        snprintf(line, sizeof(line), "fly loop probe: %s position at obj+%X -> %08X +%X", f->name, x * 4, p1, y * 4);
                                        host->log(host->context, line); found++;
                                    }
                                    const uint32_t p2 = b1[y];
                                    if (p2 < 0x10000u || p2 >= 0x04000000u || (p2 & 3u) || p2 == f->obj || p2 == p1 || reads >= 600u ||
                                        !host->read_guest(host->context, p2, c1, sizeof(c1))) continue;
                                    reads++;
                                    for (unsigned z = 0; z + 3 <= 0x400 / 4 && found < 16; z++) {
                                        const float *q2 = (const float *)&c1[z];
                                        if (fabsf(q2[0] - p0[0]) < 0.01f && fabsf(q2[1] - p0[1]) < 0.01f && fabsf(q2[2] - p0[2]) < 0.01f) {
                                            snprintf(line, sizeof(line), "fly loop probe: %s position at obj+%X -> %08X +%X -> %08X +%X",
                                                     f->name, x * 4, p1, y * 4, p2, z * 4);
                                            host->log(host->context, line); found++;
                                        }
                                    }
                                }
                            }
                        }
                        snprintf(line, sizeof(line), "fly loop probe: %s at (%.2f, %.2f, %.2f): %u place(s) found besides +08",
                                 f->name, p0[0], p0[1], p0[2], found);
                        host->log(host->context, line);
                    }
                    /* start where it stands: the nearest point of the loop */
                    float pos[3] = {0}, best = 1e30f;
                    host->read_guest(host->context, f->obj + WORLDOBJECT_POSITION, pos, 12);
                    float run = 0;
                    for (int k = 0; k < f->n; k++) {
                        float dx = f->pts[3 * k] - pos[0], dz = f->pts[3 * k + 2] - pos[2];
                        if (dx * dx + dz * dz < best) { best = dx * dx + dz * dz; f->at = run; }
                        run += f->len[k];
                    }
                }
            }
            f->look = 1.0;
        }
        if (!f->obj) continue;
        /* still that object? asked of the game each frame (its handle), never
         * a pointer kept from before: freed memory keeps the old handle */
        {
            MercGuestCall hc;
            if (!guest(MF_FN_World_ObjectFromHandle, 0, 1, &f->handle, &hc) || hc.eax != f->obj) {
                f->obj = 0;
                f->look = 0;
                continue;
            }
        }
        uint16_t flags = 0;
        uint32_t actor = 0;
        host->read_guest(host->context, f->obj + MF_OFF_WorldObject_flags, &flags, 2);
        host->read_guest(host->context, f->obj + MF_OFF_WorldObject_actor, &actor, 4);
        if (!(flags & FLAG_PLAIN) && !actor) {
            if (!f->logged) {
                f->logged = 1;
                char line[160];
                snprintf(line, sizeof(line), "fly loop: %s is neither plain scenery nor has an actor, so it isn't moved", f->name);
                host->log(host->context, line);
            }
            continue;
        }
        f->at += f->speed * (float)dt;
        if (f->at >= f->total) f->at -= f->total;
        float pos[3], ahead[3];
        fly_point(f, f->at, pos);
        fly_point(f, f->at + 40.0f, ahead);
        float yaw = atan2f(ahead[0] - pos[0], ahead[2] - pos[2]);
        int ok;
        if (actor) {
            /* 0.335: a plain object's +08 moves but what's drawn doesn't (0.333-0.334:
             * the drones' loops ran, nothing else holds their position). An object
             * with an actor (a prop: give the drone's map object an actor-backed
             * template) is moved through the actor, as the game moves the things it
             * draws: its set-position and set-yaw (vtable +0x64, +0x68) every frame */
            uint32_t scratch = 0, size = 0;
            ok = host->guest_scratch(host->context, &scratch, &size) && size >= 16;
            if (ok) {
                host->write_guest(host->context, scratch, pos, 12);
                vcall(actor, ACTOR_VT_SET_POSITION, 1, &scratch, &ok);
                if (ok) {
                    uint32_t bitsyaw;
                    memcpy(&bitsyaw, &yaw, 4);
                    vcall(actor, ACTOR_VT_SET_YAW, 1, &bitsyaw, &ok);
                }
            }
            if (!f->logged) {
                f->logged = 1;
                char line[160];
                snprintf(line, sizeof(line), "fly loop: %s has an actor (%08X): moved through it", f->name, actor);
                host->log(host->context, line);
            }
        } else {
            ok = live_apply(f->obj, pos, yaw, 0);
        }
        /* 0.333: every 10 s (12 times): where the loop is, what was asked and
         * where the object is, how often it was found again. A drone never
         * seen flying: this says whether the loop runs, the move takes and
         * it only isn't drawn there, or it keeps starting over */
        if ((f->report -= dt) <= 0 && f->reports < 12u) {
            f->report = 10.0;
            f->reports++;
            float got[3] = {0};
            host->read_guest(host->context, f->obj + WORLDOBJECT_POSITION, got, 12);
            char line[220];
            snprintf(line, sizeof(line), "fly loop: %s %u: %.0f of %.0f m round, asked (%.1f, %.1f, %.1f), it's at (%.1f, %.1f, %.1f), found %u time(s)",
                     f->name, f->reports, f->at, f->total, pos[0], pos[1], pos[2], got[0], got[1], got[2], f->finds);
            host->log(host->context, line);
        }
        if (f->logged < 2) {
            /* once: did the move take (the object's own position after it) */
            float got[3] = {0};
            host->read_guest(host->context, f->obj + WORLDOBJECT_POSITION, got, 12);
            char line[200];
            snprintf(line, sizeof(line), "fly loop: %s moving (%s): asked (%.1f, %.1f, %.1f), it's at (%.1f, %.1f, %.1f)",
                     f->name, ok ? "the move took" : "the move was refused", pos[0], pos[1], pos[2], got[0], got[1], got[2]);
            host->log(host->context, line);
            f->logged = 2;
        }
    }
}

static int MERC_PLUGIN_CALL cmd_fly_loop(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    char name[48];
    uint32_t top[2] = {L, 1};
    int n = (int)lua_call(MF_FN_lua_gettop, 1, top);
    if (n < 2 || !lua_arg_text(L, 1, name, sizeof(name)) || !name[0]) return 0;
    float v[2 + 3 * FLY_POINTS];
    int count = n - 1 < 1 + 3 * FLY_POINTS ? n - 1 : 1 + 3 * FLY_POINTS;
    for (int k = 0; k < count; k++) {
        uint32_t a[2] = {L, (uint32_t)(k + 2)};
        if (!guest(MF_FN_Lua_ArgNumber, 0, 2, a, &c)) return 0;
        v[k] = (float)c.st0;
    }
    unsigned i = 0;
    while (i < flyer_count && _stricmp(flyers[i].name, name) != 0) i++;
    if (i == flyer_count) {
        if (flyer_count >= FLY_MAX) return 0;
        flyer_count++;
    }
    Flyer *f = &flyers[i];
    memset(f, 0, sizeof(*f));
    snprintf(f->name, sizeof(f->name), "%s", name);
    f->speed = v[0];
    f->n = (count - 1) / 3;
    if (f->n < 2 && f->speed > 0) return 0;
    memcpy(f->pts, v + 1, sizeof(float) * 3 * f->n);
    for (int k = 0; k < f->n; k++) {
        const float *a = f->pts + 3 * k, *b = f->pts + 3 * ((k + 1) % f->n);
        float dx = b[0] - a[0], dy = b[1] - a[1], dz = b[2] - a[2];
        f->len[k] = sqrtf(dx * dx + dy * dy + dz * dz);
        f->total += f->len[k];
    }
    char line[160];
    snprintf(line, sizeof(line), "fly loop: %s on %d points, %.0f m round, at %.1f m/s", name, f->n, f->total, f->speed);
    host->log(host->context, line);
    uint32_t args[2] = {L, 1};
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
}

/* Foundry_RemoveObject(handle): takes away an object Foundry_FindPlaced
 * found (a thrown pager once a placed one stands in for it). True if it
 * could. */
static int MERC_PLUGIN_CALL cmd_remove_object(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    uint32_t a[2] = {L, 1};
    int ok = 0;
    if ((int)lua_call(MF_FN_lua_gettop, 1, a) >= 1 && guest(MF_FN_Lua_ArgNumber, 0, 2, a, &c)) {
        uint32_t obj = find_slot_object((uint32_t)c.st0);
        float none[3] = {0, 0, 0};
        ok = obj && live_apply(obj, none, 0, 1);
    }
    uint32_t args[2] = {L, (uint32_t)ok};
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
}

/* Foundry_ActorTemplate(actor): the template an object was spawned from,
 * as its name (templates.tsv) or "0x<hash>" when the list doesn't name it;
 * nothing for an object whose property block is no template's (placed
 * scenery, a layer's own object). Spawned objects share their template's
 * block (WorldObject +template), so it's found in g_TemplateTable's blocks.
 * Foundry_SpawnTemplate(template, name, x, y, z): spawns a template (a
 * name, or "0x<hash>" from Foundry_ActorTemplate) under a new name, facing
 * +z (Actor_SetYaw after); true if it spawned. For an HQ's parking spot:
 * the vehicle the player went in with, after a contract reloads the level. */
static int MERC_PLUGIN_CALL cmd_actor_template(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    uint32_t args[2] = {L, 1}, obj = 0, block = 0;
    if ((int)lua_call(MF_FN_lua_gettop, 1, args) < 1) return 0;
    if (guest(MF_FN_Lua_ArgActor, 0, 2, args, &c)) obj = c.eax;
    if (!obj || !host->read_guest(host->context, obj + MF_OFF_WorldObject_template, &block, 4) || !block) return 0;
    static uint32_t blocks[0x1000], hashes[0x1000];
    if (!host->read_guest(host->context, MF_VAR_g_TemplateTable + 8, blocks, sizeof(blocks)) ||
        !host->read_guest(host->context, MF_VAR_g_TemplateTable + 0x4008, hashes, sizeof(hashes))) return 0;
    uint32_t hash = 0;
    for (unsigned i = 0; i < 0x1000 && !hash; i++)
        if (hashes[i] && blocks[i] == block) hash = hashes[i];
    if (!hash) return 0;
    char text[72];
    const char *name = template_name(hash);
    if (name) snprintf(text, sizeof(text), "%s", name);
    else snprintf(text, sizeof(text), "0x%08X", hash);
    uint32_t scratch, size;
    if (!host->guest_scratch(host->context, &scratch, &size) || size < sizeof(text)) return 0;
    host->write_guest(host->context, scratch, text, strlen(text) + 1);
    uint32_t push[2] = {L, scratch};
    guest(MF_FN_lua_pushstring, 0, 2, push, &c);
    return 1;
}

/* Foundry_ActorModel(actor): the model an object draws (its GeometryFile),
 * in lower case (the game's Lua has no string library); nothing if none.
 * For HQ parking's fallback: a layer's own vehicle has no template's block,
 * so the map script looks its model up in the map's own templates. */
/* Foundry_PhysicsProbe(vehicle): where a vehicle's physics body keeps its
 * rotation and position (0.317, for a motorbike held upright: Havok's body,
 * not the world object, is what the physics moves). Reads the object's own
 * rotation (+0x14, a quaternion) and position (+0x08), then looks through
 * the vehicle (its actor, 0x1200 bytes) and everything it points to, two
 * steps deep, for the same four floats (either sign) and the same three, and
 * logs each place found ("physics probe: rotation at actor+X -> +Y ..."), up
 * to 24. Call it on a vehicle that's moving and turned a little, so the
 * numbers aren't trivial. */
static int probe_quat_at(const float *a, const float *q)
{
    float d1 = 0, d2 = 0;
    for (int k = 0; k < 4; k++) { d1 += fabsf(a[k] - q[k]); d2 += fabsf(a[k] + q[k]); }
    return d1 < 2e-3f || d2 < 2e-3f;
}

static int MERC_PLUGIN_CALL cmd_physics_probe(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    uint32_t args[2] = {L, 1}, obj = 0, actor = 0;
    if (guest(MF_FN_Lua_ArgActor, 0, 2, args, &c)) obj = c.eax;
    if (!obj || !host->read_guest(host->context, obj + MF_OFF_WorldObject_actor, &actor, 4) || !actor) {
        host->log(host->context, "physics probe: no such vehicle");
        return 0;
    }
    float pos[3], q[4];
    host->read_guest(host->context, obj + WORLDOBJECT_POSITION, pos, 12);
    host->read_guest(host->context, obj + 0x14u, q, 16);
    char line[220];
    snprintf(line, sizeof(line), "physics probe: object %08X actor %08X at (%.2f, %.2f, %.2f), rotation (%.3f, %.3f, %.3f, %.3f)",
             obj, actor, pos[0], pos[1], pos[2], q[0], q[1], q[2], q[3]);
    host->log(host->context, line);
    {   /* 0.337: the rigid body as the code reads it (physics at actor+0x2C0,
         * its body at +0x1C, the body's core at +0x3C: rotation +0x30, then
         * likely the linear and angular velocity +0x40/+0x50, position +0xB0),
         * beside the object's own, so one ride says whether it's the state to
         * correct for a bike held upright */
        uint32_t body = 0, core = 0;
        float cq[4] = {0}, cv[4] = {0}, cw[4] = {0}, cp[3] = {0};
        if (host->read_guest(host->context, actor + 0x2C0u + 0x1Cu, &body, 4) && body &&
            host->read_guest(host->context, body + 0x3Cu, &core, 4) && core) {
            host->read_guest(host->context, core + 0x30u, cq, 16);
            host->read_guest(host->context, core + 0x40u, cv, 16);
            host->read_guest(host->context, core + 0x50u, cw, 16);
            host->read_guest(host->context, core + 0xB0u, cp, 12);
        }
        char l2[300];
        snprintf(l2, sizeof(l2), "physics probe: body %08X core %08X: rotation +30 (%.3f, %.3f, %.3f, %.3f), +40 (%.2f, %.2f, %.2f), "
                 "+50 (%.2f, %.2f, %.2f), position +B0 (%.2f, %.2f, %.2f)", body, core, cq[0], cq[1], cq[2], cq[3],
                 cv[0], cv[1], cv[2], cw[0], cw[1], cw[2], cp[0], cp[1], cp[2]);
        host->log(host->context, l2);
    }
    static uint32_t a[0x1200 / 4], b[0x400 / 4], cc[0x400 / 4];
    if (!host->read_guest(host->context, actor, a, sizeof(a))) return 0;
    int found = 0;
    unsigned second = 0;                          /* second-step reads, capped */
    for (unsigned i = 0; i < 0x1200 / 4 && found < 24; i++) {
        /* in the actor itself */
        if (i + 4 <= 0x1200 / 4 && probe_quat_at((const float *)&a[i], q)) {
            snprintf(line, sizeof(line), "physics probe: rotation at actor+%X", i * 4);
            host->log(host->context, line); found++;
        }
        uint32_t p1 = a[i];
        if (p1 < 0x10000u || p1 >= 0x04000000u || (p1 & 3u) || !host->read_guest(host->context, p1, b, sizeof(b))) continue;
        for (unsigned j = 0; j + 4 <= 0x400 / 4 && found < 24; j++) {
            const float *f = (const float *)&b[j];
            if (probe_quat_at(f, q)) {
                snprintf(line, sizeof(line), "physics probe: rotation at actor+%X -> %08X +%X", i * 4, p1, j * 4);
                host->log(host->context, line); found++;
            } else if (j + 3 <= 0x400 / 4 && fabsf(f[0] - pos[0]) < 0.05f && fabsf(f[1] - pos[1]) < 0.05f && fabsf(f[2] - pos[2]) < 0.05f) {
                snprintf(line, sizeof(line), "physics probe: position at actor+%X -> %08X +%X", i * 4, p1, j * 4);
                host->log(host->context, line); found++;
            }
            uint32_t p2 = b[j];
            if (p2 < 0x10000u || p2 >= 0x04000000u || (p2 & 3u) || p2 == actor || p2 == obj || second >= 4000u ||
                !host->read_guest(host->context, p2, cc, sizeof(cc))) continue;
            second++;
            for (unsigned k = 0; k + 4 <= 0x400 / 4 && found < 24; k++)
                if (probe_quat_at((const float *)&cc[k], q)) {
                    snprintf(line, sizeof(line), "physics probe: rotation at actor+%X -> %08X +%X -> %08X +%X",
                             i * 4, p1, j * 4, p2, k * 4);
                    host->log(host->context, line); found++;
                }
        }
    }
    if (!found) host->log(host->context, "physics probe: the rotation wasn't found within two steps of the vehicle");
    push_number(L, (double)found);
    return 1;
}

static int MERC_PLUGIN_CALL cmd_actor_model(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    uint32_t args[2] = {L, 1}, obj = 0, handle = 0;
    if ((int)lua_call(MF_FN_lua_gettop, 1, args) < 1) return 0;
    if (guest(MF_FN_Lua_ArgActor, 0, 2, args, &c)) obj = c.eax;
    if (!obj) return 0;
    host->read_guest(host->context, obj + MF_OFF_WorldObject_handle, &handle, 4);
    char m[64];
    object_model(obj, handle, m, sizeof(m));
    if (!m[0]) return 0;
    for (char *q = m; *q; q++) *q = (char)tolower((unsigned char)*q);
    uint32_t scratch, size;
    if (!host->guest_scratch(host->context, &scratch, &size) || size < sizeof(m)) return 0;
    host->write_guest(host->context, scratch, m, strlen(m) + 1);
    uint32_t push[2] = {L, scratch};
    guest(MF_FN_lua_pushstring, 0, 2, push, &c);
    return 1;
}

static int MERC_PLUGIN_CALL cmd_spawn_template(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    char tmpl[72], name[64];
    int ok = 0;
    if (lua_arg_text(L, 1, tmpl, sizeof(tmpl)) && lua_arg_text(L, 2, name, sizeof(name)) && tmpl[0] && name[0]) {
        float pos[3] = {0, 0, 0};
        for (int k = 0; k < 3; k++) {
            uint32_t a[2] = {L, (uint32_t)(3 + k)};
            if (guest(MF_FN_Lua_ArgNumber, 0, 2, a, &c)) pos[k] = (float)c.st0;
        }
        uint32_t hash = (tmpl[0] == '0' && (tmpl[1] == 'x' || tmpl[1] == 'X')) ? (uint32_t)strtoul(tmpl + 2, NULL, 16)
                                                                              : hash_name(tmpl);
        uint32_t scratch, size;
        if (hash && host->guest_scratch(host->context, &scratch, &size) && size >= 160) {
            float m[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, pos[0], pos[1], pos[2], 1};
            host->write_guest(host->context, scratch, m, sizeof(m));
            host->write_guest(host->context, scratch + 64, name, strlen(name) + 1);
            uint32_t args[5] = {hash, scratch + 64, scratch, 0, 0};
            ok = guest(MF_FN_Spawner_SpawnTemplate, MF_VAR_g_Spawner, 5, args, &c) && c.eax;
        }
        char line[200];
        snprintf(line, sizeof(line), "spawn template: %s as %s at %.1f, %.1f, %.1f%s", tmpl, name, pos[0], pos[1],
                 pos[2], ok ? "" : " failed");
        host->log(host->context, line);
    }
    uint32_t args[2] = {L, (uint32_t)ok};
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
}

/* A map's own store (maps json "shop"). The PDA shop (0x34ABC8) keeps one
 * unlocked list for the whole save (+0x1194, 12 bytes each: catalogue
 * index, stock/price, new; count +0x1794; per-category counts +0x1798) over
 * one catalogue (+0x190, 32 bytes each: +8 template hash, +0x1C category;
 * count +0x1190). Foundry_MapShop(template, ...) until the next level loads
 * hides every unlocked item not named; on a level that doesn't call it,
 * items whose template isn't loaded there (another map's) are hidden. Hidden
 * entries are taken out of the list and put back just before the game
 * saves it (Utility_SavePlayerData), then taken out again, so the save keeps
 * every unlock. Looked at each half second (unlocks and the save's restore
 * add entries). */
#define SHOP 0x34ABC8u
#define SHOP_LIST (SHOP + 0x1194u)
#define SHOP_LIST_COUNT (SHOP + 0x1794u)
#define SHOP_CAT_COUNTS (SHOP + 0x1798u)
#define SHOP_CATALOGUE (SHOP + 0x190u)
#define SHOP_CATALOGUE_COUNT (SHOP + 0x1190u)
#define SHOP_MAP_MAX 64
static uint32_t shop_map[SHOP_MAP_MAX];
static unsigned shop_map_count;
#define SHOP_NOTICE_LIST 0x87BF80u   /* count + 31 hashes the port lets 0xC8560 queue (0.331) */
static int shop_map_on;                       /* this level named its store */
typedef struct { uint32_t entry[3], hash; } ShopHidden;
static ShopHidden shop_hidden[128];
static unsigned shop_hidden_count;
static double shop_timer;
static int shop_saving;
static int shop_hooked;                       /* hiding is safe only with the save hook */

static int shop_catalogue(uint32_t *hashes, uint32_t *cats, uint32_t *count)
{
    static uint32_t raw[128 * 8];
    /* an empty list or catalogue is read as nothing: a 0-byte read is refused
     * (the store, after a save's reload with its list empty, answered nothing
     * for every item and stayed closed, 0.312) */
    if (!host->read_guest(host->context, SHOP_CATALOGUE_COUNT, count, 4) || *count > 128 ||
        (*count && !host->read_guest(host->context, SHOP_CATALOGUE, raw, *count * 32))) return 0;
    for (uint32_t i = 0; i < *count; i++) { hashes[i] = raw[i * 8 + 2]; cats[i] = raw[i * 8 + 7]; }
    return 1;
}

static int shop_allowed(uint32_t hash)
{
    if (shop_map_on) {
        for (unsigned i = 0; i < shop_map_count; i++)
            if (shop_map[i] == hash) return 1;
        return 0;
    }
    return template_slot(hash) >= 0;          /* loaded here (another map's own isn't) */
}

static void shop_cat_add(uint32_t cat, int by)
{
    uint32_t n = 0;
    if (cat > 2) return;
    host->read_guest(host->context, SHOP_CAT_COUNTS + 4 * cat, &n, 4);
    n = (uint32_t)((int)n + by);
    if ((int)n < 0) n = 0;
    host->write_guest(host->context, SHOP_CAT_COUNTS + 4 * cat, &n, 4);
}

/* The game's "new shop item" notices (0xC8560: up to 8 template hashes at
 * +0xE37C of the PDA display object [0x34ABC8+0x24], count at +0xE378): one
 * queued for an item hidden on this map is dropped (0.322). Gaza's first
 * Settlers contract unlocked a game item at its end (the retail slot's own
 * reward), hidden here, and its notice showed blank before the map's own. */
static void shop_drop_hidden_notices(void)
{
    uint32_t pda = 0, n = 0, q[8];
    if (!shop_hidden_count || !host->read_guest(host->context, SHOP + 0x24u, &pda, 4) || !pda ||
        !host->read_guest(host->context, pda + 0xE378u, &n, 4) || !n || n > 8u ||
        !host->read_guest(host->context, pda + 0xE37Cu, q, n * 4)) return;
    uint32_t kept = 0;
    for (uint32_t i = 0; i < n; i++) {
        int hidden = 0;
        for (unsigned k = 0; k < shop_hidden_count && !hidden; k++) hidden = shop_hidden[k].hash == q[i];
        if (!hidden) { q[kept++] = q[i]; continue; }
        const char *name = name_for_hash(q[i]);
        char line[160];
        snprintf(line, sizeof(line), "map shop: the \"new item\" notice for %s (%08X) dropped: it's hidden on this map",
                 name ? name : "an item", q[i]);
        host->log(host->context, line);
    }
    if (kept == n) return;
    host->write_guest(host->context, pda + 0xE37Cu, q, kept * 4);
    host->write_guest(host->context, pda + 0xE378u, &kept, 4);
}

static void shop_hide(void)
{
    static uint32_t hashes[128], cats[128], list[128 * 3];
    uint32_t ccount = 0, n = 0;
    if (!shop_catalogue(hashes, cats, &ccount) || !ccount) return;
    if (!host->read_guest(host->context, SHOP_LIST_COUNT, &n, 4) || n > 128 ||
        (n && !host->read_guest(host->context, SHOP_LIST, list, n * 12))) return;
    uint32_t kept = 0, hid = 0;
    for (uint32_t k = 0; k < n; k++) {
        uint32_t idx = list[k * 3];
        uint32_t hash = idx < ccount ? hashes[idx] : 0;
        if (!hash || shop_allowed(hash) || shop_hidden_count >= 128) {
            if (kept != k) memmove(&list[kept * 3], &list[k * 3], 12);
            kept++;
            continue;
        }
        ShopHidden *h = &shop_hidden[shop_hidden_count++];
        memcpy(h->entry, &list[k * 3], 12);
        h->hash = hash;
        shop_cat_add(cats[idx], -1);
        hid++;
    }
    if (!hid) { shop_drop_hidden_notices(); return; }
    host->write_guest(host->context, SHOP_LIST, list, kept * 12);
    host->write_guest(host->context, SHOP_LIST_COUNT, &kept, 4);
    if (!shop_saving) {
        char line[200], names[120] = "";
        int used = 0;
        for (unsigned i = shop_hidden_count - hid, m = 0; i < shop_hidden_count && m < 3u && used < (int)sizeof(names) - 2; i++, m++) {
            const char *nm = name_for_hash(shop_hidden[i].hash);
            used += nm ? snprintf(names + used, sizeof(names) - used, "%s%s", m ? ", " : "", nm)
                       : snprintf(names + used, sizeof(names) - used, "%s%08X", m ? ", " : "", shop_hidden[i].hash);
        }
        snprintf(line, sizeof(line), "map shop: %u items hidden on this map (%u shown): %s%s", hid, kept, names,
                 hid > 3u ? ", ..." : "");
        host->log(host->context, line);
    }
    shop_drop_hidden_notices();
}

/* all hidden entries back in the list, or (only_allowed) those allowed now:
 * a template table still loading hides the game's own for a moment */
static void shop_unhide(int only_allowed)
{
    static uint32_t hashes[128], cats[128], list[128 * 3];
    uint32_t ccount = 0, n = 0;
    if (!shop_hidden_count) return;
    if (!shop_catalogue(hashes, cats, &ccount) ||
        !host->read_guest(host->context, SHOP_LIST_COUNT, &n, 4) || n > 128 ||
        (n && !host->read_guest(host->context, SHOP_LIST, list, n * 12))) return;
    unsigned left = 0;
    for (unsigned i = 0; i < shop_hidden_count; i++) {
        if (n >= 128 || (only_allowed && !shop_allowed(shop_hidden[i].hash))) {
            shop_hidden[left++] = shop_hidden[i];
            continue;
        }
        int there = 0;
        for (uint32_t k = 0; k < n && !there; k++) {
            uint32_t idx = list[k * 3];
            there = idx < ccount && hashes[idx] == shop_hidden[i].hash;
        }
        if (there) continue;                  /* unlocked again since */
        memcpy(&list[n * 3], shop_hidden[i].entry, 12);
        n++;
        uint32_t idx = shop_hidden[i].entry[0];
        if (idx < ccount) shop_cat_add(cats[idx], 1);
    }
    shop_hidden_count = left;
    host->write_guest(host->context, SHOP_LIST, list, n * 12);
    host->write_guest(host->context, SHOP_LIST_COUNT, &n, 4);
}

/* Foundry_ShopUnlocked(template): true if the PDA shop's unlocked list has
 * the item (shown or hidden here), nil if not; false if the catalogue has no
 * such item. The map's store asks it each 5 s: the save's player data, read
 * after the map's script as a level loads (0x189490), replaces the list, so
 * an item unlocked in ScriptInit was gone again while the script's
 * once-per-save mark stayed (Gaza's store, empty, showed the locked page:
 * 0xCE9F0 shows it when no tab has an item, as when the lock is on). */
static unsigned shop_unlocked_logs;            /* Foundry_ShopUnlocked's lines this level */
static int MERC_PLUGIN_CALL cmd_shop_unlocked(void *ud, uint32_t L)
{
    static uint32_t hashes[128], cats[128], list[128 * 3];
    char t[72];
    uint32_t ccount = 0, n = 0;
    (void)ud;
    if (!lua_arg_text(L, 1, t, sizeof(t))) return 0;
    if (!shop_catalogue(hashes, cats, &ccount) ||
        !host->read_guest(host->context, SHOP_LIST_COUNT, &n, 4) || n > 128 ||
        (n && !host->read_guest(host->context, SHOP_LIST, list, n * 12))) {
        if (shop_unlocked_logs < 24) {
            char line[160];
            shop_unlocked_logs++;
            snprintf(line, sizeof(line), "map shop: asked about %s, but the shop couldn't be read (catalogue %u, list %u)",
                     t, ccount, n);
            host->log(host->context, line);
        }
        return 0;
    }
    uint32_t want = hash_name(t);
    int known = 0, have = 0, hidden = 0;
    for (uint32_t i = 0; i < ccount; i++) known |= hashes[i] == want;
    for (uint32_t k = 0; k < n && !have; k++) have = list[k * 3] < ccount && hashes[list[k * 3]] == want;
    for (unsigned i = 0; i < shop_hidden_count && !have; i++) have = hidden = shop_hidden[i].hash == want;
    /* why an item isn't unlocked again, once per item a level (0.304: after
     * a Gaza save's return none of its 7 came back, list and hidden empty) */
    if (have && shop_unlocked_logs < 24) {
        char line[160];
        shop_unlocked_logs++;
        snprintf(line, sizeof(line), "map shop: %s is unlocked already (%s; %u in the list)", t,
                 hidden ? "hidden here" : "in the list", n);
        host->log(host->context, line);
    }
    if (!known) {
        static uint32_t warned[16];
        unsigned w = 0;
        while (w < 16 && warned[w] && warned[w] != want) w++;
        if (w < 16 && !warned[w]) {
            warned[w] = want;
            char line[160];
            snprintf(line, sizeof(line), "map shop: %s isn't in the shop's catalogue (%u items): the mod's "
                     "DATAPOD/itemshop.ini didn't load?", t, ccount);
            host->log(host->context, line);
        }
    }
    if (!have && !known) return 0;
    uint32_t args[2] = {L, (uint32_t)have};
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
}

/* Foundry_ShopRestore(template): the item back in the PDA shop's unlocked
 * list without the "new support item" popup: 0xCDA60 (Shop_UnlockItem's,
 * RsDataPodVM) with its second argument set, as the save's own restore calls
 * it (0xCDC20): the entry isn't marked new (+0x119C) and no notice is queued
 * (0xC8560 skipped). For a map's store putting back what the save had
 * unlocked: each level load after the first showed the same popups again
 * (0.305, Gaza's settlers contracts). True if the item is in the list after. */
#define SHOP_UNLOCK_FN 0x000CDA60u
/* 0.331: the items restored on this level, kept in the list and in stock
 * every half second (shop_restore_poll): a trip's load empties the game's
 * list and its read-back may come after the map's script restored them, so
 * the store showed nothing for the first seconds after a trip (Gaza's,
 * looked at 5-10 s in, 0.330). Forgotten as a level loads. */
#define SHOP_WANT_MAX 32
static uint32_t shop_want[SHOP_WANT_MAX];
static unsigned shop_want_count;
static int shop_restore_hash(uint32_t hash);
static int MERC_PLUGIN_CALL cmd_shop_restore(void *ud, uint32_t L)
{
    char t[72];
    (void)ud;
    if (!lua_arg_text(L, 1, t, sizeof(t))) return 0;
    const uint32_t h = hash_name(t);
    unsigned w = 0;
    while (w < shop_want_count && shop_want[w] != h) w++;
    if (w == shop_want_count && shop_want_count < SHOP_WANT_MAX) shop_want[shop_want_count++] = h;
    int ok = shop_restore_hash(h);
    uint32_t push[2] = {L, (uint32_t)ok};
    lua_call(MF_FN_Lua_PushBool, 2, push);
    return 1;
}

static int shop_restore_hash(uint32_t hash)
{
    MercGuestCall c;
    uint32_t args[2];
    args[0] = hash;
    args[1] = 1;
    int ok = guest(SHOP_UNLOCK_FN, SHOP, 2, args, &c) && (int32_t)c.eax >= 0;
    /* 0.328: and in stock. The restore flag also sets the item's stock slot
     * (+4 of its list entry) to -1, out of stock, and a tab counts only items
     * in stock (0xC8AD0), so a store whose items all came back this way had
     * no tab with items and showed nothing (page 0: Gaza's after every trip,
     * "7 in the list, tabs 3 3 1", 0.327). The game's own 0xC8B60(index, 0)
     * puts the catalogue's DefaultPrice back in the slot. */
    if (ok) {
        static uint32_t hashes[128], cats[128], list[128 * 3];
        uint32_t ccount = 0, n = 0;
        if (shop_catalogue(hashes, cats, &ccount) &&
            host->read_guest(host->context, SHOP_LIST_COUNT, &n, 4) && n <= 128 &&
            (!n || host->read_guest(host->context, SHOP_LIST, list, n * 12))) {
            for (uint32_t k = 0; k < n; k++) {
                const uint32_t idx = list[k * 3];
                if (idx >= ccount || hashes[idx] != args[0] || (int32_t)list[k * 3 + 1] >= 0) continue;
                uint32_t a[2] = {k, 0};
                guest(0x000C8B60u, SHOP, 2, a, &c);
            }
        }
    }
    /* as an unlock does, without its popup: a store with no page yet (+0x19AC
     * 0, as after a level load) gets its list page (0xCD6C0(1), which the
     * restore flag skips), or the PDA showed it closed with its items in
     * (0.314: Gaza's store after a save's return, 7 items back, tabs read 0) */
    uint32_t page = 0, one = 1;
    if (ok && host->read_guest(host->context, SHOP + 0x19ACu, &page, 4) && page == 0)
        guest(0x000CD6C0u, SHOP, 1, &one, &c);
    return ok;
}

/* every half second: each item restored on this level back in the list if
 * the game's read-back dropped it, and back in stock if it came back out */
static void shop_restore_poll(void)
{
    static uint32_t hashes[128], cats[128], list[128 * 3];
    static unsigned said;
    uint32_t ccount = 0, n = 0;
    if (!shop_want_count || shop_saving || !shop_catalogue(hashes, cats, &ccount) ||
        !host->read_guest(host->context, SHOP_LIST_COUNT, &n, 4) || n > 128 ||
        (n && !host->read_guest(host->context, SHOP_LIST, list, n * 12))) return;
    for (unsigned w = 0; w < shop_want_count; w++) {
        int at = -1, hidden = 0;
        for (uint32_t k = 0; k < n && at < 0; k++)
            if (list[k * 3] < ccount && hashes[list[k * 3]] == shop_want[w]) at = (int)k;
        for (unsigned i = 0; i < shop_hidden_count && !hidden; i++) hidden = shop_hidden[i].hash == shop_want[w];
        if (hidden) continue;
        if (at < 0) {
            shop_restore_hash(shop_want[w]);
            if (said < 16u) {
                said++;
                char line[120];
                snprintf(line, sizeof(line), "map shop: %08X back in the list again (the game's list came back without it)", shop_want[w]);
                host->log(host->context, line);
            }
        } else if ((int32_t)list[at * 3 + 1] < 0) {
            MercGuestCall c;
            uint32_t a[2] = {(uint32_t)at, 0};
            guest(0x000C8B60u, SHOP, 2, a, &c);
        }
    }
}

/* Foundry_MapShopOpen(): the PDA shop open on this map whatever the game's
 * lock (Shop_SetShopLocked: the byte at shop +0x19BC, cleared by retail's
 * first Mafia contract). The lock is saved with the player data and read back
 * after the map's script runs (0xCDC20, from 0x189490), so it's looked at
 * every half second: a set lock is noted as the game's and cleared. The
 * game's goes back for each save (so the save keeps it) and as the next
 * level loads. Needs the save hook. */
#define SHOP_LOCK (SHOP + 0x19BCu)
static int shop_open_on;                       /* this level opened its store */
static uint8_t shop_lock_game;                 /* the game's lock while it's open */

static void shop_open_poll(void)
{
    uint8_t lock = 0, zero = 0;
    if (!shop_open_on || shop_saving || !host->read_guest(host->context, SHOP_LOCK, &lock, 1) || !lock) return;
    shop_lock_game = lock;
    host->write_guest(host->context, SHOP_LOCK, &zero, 1);
    host->log(host->context, "map shop: open here (the game's lock is kept for the saves and other levels)");
}

static void shop_open_put_back(void)
{
    if (shop_open_on) host->write_guest(host->context, SHOP_LOCK, &shop_lock_game, 1);
}

static int shop_empty_logged;
static double shop_level_time;                 /* seconds into the level, for the 10 s report */
static void shop_poll(double dt)
{
    if (!shop_hooked) return;
    shop_open_poll();
    if (shop_level_time >= 0 && (shop_level_time += dt) >= 10.0) {
        /* what the PDA's store has to go on, once per level (0.301: Gaza's
         * looked closed after a save came back to it): the lock, the list and
         * the tab counts (0xCE9F0 shows the closed page when no tab has one) */
        uint8_t lock = 0;
        uint32_t n = 0, cats[3] = {0};
        char line[200];
        shop_level_time = -1;
        host->read_guest(host->context, SHOP_LOCK, &lock, 1);
        host->read_guest(host->context, SHOP_LIST_COUNT, &n, 4);
        host->read_guest(host->context, SHOP_CAT_COUNTS, cats, sizeof(cats));
        snprintf(line, sizeof(line), "map shop: 10 s in: lock %u%s, %u items in the list, tabs %u %u %u, %u hidden%s",
                 lock, shop_open_on ? " (opened here)" : "", n, cats[0], cats[1], cats[2], shop_hidden_count,
                 shop_map_on ? " (this map's store)" : "");
        host->log(host->context, line);
    }
    if ((shop_timer += dt) < 0.5) return;
    shop_timer = 0;
    shop_unhide(1);
    shop_hide();
    shop_restore_poll();
    int empty_logged = shop_empty_logged;
    uint32_t n = 1;
    host->read_guest(host->context, SHOP_LIST_COUNT, &n, 4);
    if (shop_map_on && !n && !empty_logged) {
        empty_logged = 1;
        host->log(host->context, "map shop: none of this map's items unlocked yet, so the PDA shows its locked page");
    } else if (n) {
        empty_logged = 0;
    }
    shop_empty_logged = empty_logged;
}

static void MERC_PLUGIN_CALL save_before(void *ud, MercHookContext *c)
{
    (void)ud; (void)c;
    shop_saving = 1;
    shop_unhide(0);
    shop_open_put_back();
}

static void MERC_PLUGIN_CALL save_after(void *ud, MercHookContext *c)
{
    (void)ud; (void)c;
    shop_hide();
    shop_saving = 0;
    shop_open_poll();
}

static void shop_level_reset(void)
{
    shop_open_put_back();
    shop_open_on = 0;
    /* back in the list first, in case the game saves it after this (the
     * load empties it, and the save's restore fills it again) */
    shop_unhide(0);
    shop_hidden_count = 0;
    shop_map_on = 0;
    shop_map_count = 0;
    shop_want_count = 0;
    { uint32_t z = 0; host->write_guest(host->context, SHOP_NOTICE_LIST, &z, 4); }   /* every notice again (0.331) */
    shop_timer = 0;
    shop_empty_logged = 0;                     /* each level says so again */
    shop_unlocked_logs = 0;
    shop_level_time = 0;
}

static int MERC_PLUGIN_CALL cmd_map_shop(void *ud, uint32_t L)
{
    (void)ud;
    uint32_t a[2] = {L, 1};
    int n = (int)lua_call(MF_FN_lua_gettop, 1, a);
    shop_map_count = 0;
    for (int i = 1; i <= n && shop_map_count < SHOP_MAP_MAX; i++) {
        char t[72];
        if (lua_arg_text(L, i, t, sizeof(t)) && t[0]) shop_map[shop_map_count++] = hash_name(t);
    }
    shop_map_on = 1;
    {   /* 0.331: the port's 0xC8560 queues a "new item" notice only for these
         * (count, then up to 31 hashes at 0x87BF80): another map's or the
         * game's item unlocked here (a contract's own reward) showed its
         * notice blank before the plugin's half-second pass could drop it */
        uint32_t list[32] = {0};
        list[0] = shop_map_count < 31u ? shop_map_count : 31u;
        for (uint32_t i = 0; i < list[0]; i++) list[1 + i] = shop_map[i];
        host->write_guest(host->context, SHOP_NOTICE_LIST, list, sizeof(list));
    }
    char line[96];
    snprintf(line, sizeof(line), "map shop: this map's store sells %u items (the rest hidden here)", shop_map_count);
    host->log(host->context, line);
    shop_hide();
    uint32_t args[2] = {L, 1};
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
}

static int MERC_PLUGIN_CALL cmd_map_shop_open(void *ud, uint32_t L)
{
    (void)ud;
    if (!shop_hooked) {
        host->log(host->context, "map shop: can't open it here without the save hook (the game's lock stays)");
        return 0;
    }
    {   /* 0.321: and not closed by the shop faction's mood (the game's Mafia
         * slot: 0xCE9F0 shows the closed page while 0x95EE0 finds it not
         * friendly; Gaza's Settlers at 0 after a trip's mood swap), through a
         * byte the port reads there (0x87BF01, Patch-Generated "Store open
         * whatever the mood"); cleared as each level loads */
        uint8_t one = 1;
        host->write_guest(host->context, 0x87BF01u, &one, 1);
    }
    if (!shop_open_on) {
        uint8_t lock = 0;
        host->read_guest(host->context, SHOP_LOCK, &lock, 1);
        shop_lock_game = lock;
        shop_open_on = 1;
        host->log(host->context, lock ? "map shop: open here (the game's lock, on, is kept for the saves and other levels)"
                                      : "map shop: open here (the game's is open too)");
        uint8_t zero = 0;
        if (lock) host->write_guest(host->context, SHOP_LOCK, &zero, 1);
    }
    uint32_t args[2] = {L, 1};
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
}

/* Foundry_ShopUrl(url): the PDA shop's address bar ("http://www.menace.ru/%s",
 * 0x2E7CF8, 24 bytes: the page's name, topmenu.php and so on, goes in for %s)
 * reads url until the next level loads. At most 23 characters with exactly
 * one %s. Returns true, or nil. */
#define SHOP_URL_ADDR 0x2E7CF8u
#define SHOP_URL_SIZE 24
static char shop_url_game[SHOP_URL_SIZE];
static int shop_url_on;

static void shop_url_restore(void)
{
    if (!shop_url_on) return;
    host->write_guest(host->context, SHOP_URL_ADDR, shop_url_game, SHOP_URL_SIZE);
    shop_url_on = 0;
    host->log(host->context, "map shop address: the game's put back");
}

static int MERC_PLUGIN_CALL cmd_shop_url(void *ud, uint32_t L)
{
    char url[64], now[SHOP_URL_SIZE];
    (void)ud;
    if (!lua_arg_text(L, 1, url, sizeof(url))) return 0;
    const char *at = strchr(url, '%');
    if (strlen(url) >= SHOP_URL_SIZE || !at || at[1] != 's' || strchr(at + 1, '%')) {
        char line[160];
        snprintf(line, sizeof(line), "map shop address: '%s' isn't one (at most %d characters with one %%s)", url,
                 SHOP_URL_SIZE - 1);
        host->log(host->context, line);
        return 0;
    }
    if (!shop_url_on) {
        if (!host->read_guest(host->context, SHOP_URL_ADDR, shop_url_game, SHOP_URL_SIZE) ||
            memcmp(shop_url_game, "http://", 7) != 0) {
            host->log(host->context, "map shop address: the game's isn't where it should be; left alone");
            return 0;
        }
        shop_url_on = 1;
    }
    char buf[SHOP_URL_SIZE] = {0};
    memcpy(buf, url, strlen(url));
    host->write_guest(host->context, SHOP_URL_ADDR, buf, SHOP_URL_SIZE);
    char line[128];
    int ok = host->read_guest(host->context, SHOP_URL_ADDR, now, SHOP_URL_SIZE) && !memcmp(now, buf, SHOP_URL_SIZE);
    snprintf(line, sizeof(line), "map shop address: %s until the next level loads%s", url,
             ok ? "" : " (didn't take: the game's stays)");
    host->log(host->context, line);
    if (!ok) return 0;
    uint32_t args[2] = {L, 1};
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
}

static int MERC_PLUGIN_CALL cmd_map_captives(void *ud, uint32_t L)
{
    (void)ud;
    uint32_t args[2] = {L, 1};
    int on = (int)lua_call(MF_FN_lua_gettop, 1, args) < 1 || lua_call(MF_FN_Lua_ArgBool, 2, args) != 0;
    double delay = ANY_CAPTIVE_DELAY;
    if ((int)lua_call(MF_FN_lua_gettop, 1, args) >= 2) {
        uint32_t a2[2] = {L, 2};
        MercGuestCall c;
        if (guest(MF_FN_Lua_ArgNumber, 0, 2, a2, &c)) delay = c.st0 < 0 ? 0 : c.st0;
    }
    if (!on) {
        any_captive_set(0);
        host->log(host->context, "map captives: back to the game's (verifiable targets only)");
    } else if (delay <= 0) {
        any_captive_set(1);
        host->log(host->context, "map captives: on now (any subdued human counts as a captive until the next level loads)");
    } else {
        any_captive_wait = delay;
        char line[120];
        snprintf(line, sizeof(line), "map captives: any subdued human counts as a captive in %.0f s", delay);
        host->log(host->context, line);
    }
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
}

static int MERC_PLUGIN_CALL cmd_map_template(void *ud, uint32_t L)
{
    char from[160], to[160];
    (void)ud;
    if (!lua_arg_text(L, 1, from, sizeof(from)) || !lua_arg_text(L, 2, to, sizeof(to))) return 0;
    uint32_t f = hash_name(from), t = hash_name(to);
    unsigned i;
    for (i = 0; i < maptemplate_count && maptemplate[i].from != f; i++) {}
    if (i == maptemplate_count) {
        if (maptemplate_count == MAPTEMPLATE_MAX) return 0;
        maptemplate_count++;
    }
    if (maptemplate[i].placed && i < maptemplate_count) {
        /* asked again: the first original goes back before the new swap */
        MapTemplateSwap *s = &maptemplate[i];
        uint32_t now = 0;
        if (host->read_guest(host->context, MF_VAR_g_TemplateTable + 8 + 4 * s->slot, &now, 4) && now == s->mine)
            host->write_guest(host->context, MF_VAR_g_TemplateTable + 8 + 4 * s->slot, &s->original, 4);
    }
    maptemplate[i] = (MapTemplateSwap){f, t, 0, 0, 0, 0};
    maptemplate_place(&maptemplate[i]);       /* now, when other's layer has loaded already */
    char line[400];
    snprintf(line, sizeof(line), "map templates: %s spawns with %s's properties until the next level loads", from, to);
    host->log(host->context, line);
    uint32_t args[2] = {L, 1};
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
}

/* Foundry_MapText(key, other): key shows other's text until the next level
 * loads. Returns true, or nil when either key isn't in the game's strings. */
static int MERC_PLUGIN_CALL cmd_map_text(void *ud, uint32_t L)
{
    char key[160], other[160];
    (void)ud;
    if (!lua_arg_text(L, 1, key, sizeof(key)) || !lua_arg_text(L, 2, other, sizeof(other))) return 0;
    uint32_t table = string_table(), values = 0, text = 0, original = 0;
    if (!table) return 0;
    int to = string_slot(table, hash_name(key), &values);
    int from = string_slot(table, hash_name(other), &values);
    if (to < 0 || from < 0) {
        if (!maptext_missing++) snprintf(maptext_missed, sizeof(maptext_missed), "%s", to < 0 ? key : other);
        return 0;
    }
    if (!host->read_guest(host->context, values + 4 * (uint32_t)from, &text, 4) ||
        !host->read_guest(host->context, values + 4 * (uint32_t)to, &original, 4)) return 0;
    if (original != text) {
        unsigned i;
        for (i = 0; i < maptext_count; i++)               /* asked again: keep the first original */
            if (maptext[i].key == hash_name(key)) break;
        if (i == maptext_count) {
            if (maptext_count == MAPTEXT_MAX) return 0;
            maptext[maptext_count++] = (MapTextSwap){hash_name(key), original, text, hash_name(other)};
        } else {
            maptext[i].replaced = text;
        }
        host->write_guest(host->context, values + 4 * (uint32_t)to, &text, 4);
    }
    uint32_t args[2] = {L, 1};
    lua_call(MF_FN_Lua_PushBool, 2, args);
    return 1;
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
    if (!h->hook_guest(h->context, MF_FN_Spawner_SpawnTemplate, spawn_before, NULL, NULL))
        h->log(h->context, "spawn swap hook unavailable (run port-hooks); a map's template_overrides don't apply");
    if (!h->hook_guest(h->context, MF_FN_Spawner_SpawnTemplate, NULL, spawn_after, NULL))
        h->log(h->context, "spawn hook unavailable (run port-hooks); recent spawns won't show");
    if (!h->hook_guest(h->context, MF_FN_LuaScript_RegisterCommands, NULL, script_loaded, NULL))
        h->log(h->context, "script hook unavailable (run port-hooks); the Lua console can't find scripts");
    if (!h->hook_guest(h->context, MF_FN_World_CreateBorderWalls, border_before, NULL, NULL))
        h->log(h->context, "world border hook unavailable (run port-hooks); maps bigger than 512 cells keep "
                           "retail's walls at +-2048 m");
    if (!MERC_PLUGIN_HOST_HAS(h, add_lua_command) ||
        !h->add_lua_command(h->context, "Foundry_TrafficZone", cmd_traffic_zone, NULL) ||
        !h->add_lua_command(h->context, "Foundry_Range", cmd_range, NULL) ||
        !h->add_lua_command(h->context, "Foundry_Sample", cmd_sample, NULL) ||
        !h->add_lua_command(h->context, "Foundry_Label", cmd_label, NULL) ||
        !h->add_lua_command(h->context, "Foundry_SetHandling", cmd_set_handling, NULL) ||
        !h->add_lua_command(h->context, "Foundry_MapText", cmd_map_text, NULL) ||
        !h->add_lua_command(h->context, "Foundry_MapTexture", cmd_map_texture, NULL) ||
        !h->add_lua_command(h->context, "Foundry_HqMode", cmd_hq_mode, NULL) ||
        !h->add_lua_command(h->context, "Foundry_MapTemplate", cmd_map_template, NULL) ||
        !h->add_lua_command(h->context, "Foundry_MapCaptives", cmd_map_captives, NULL) ||
        !h->add_lua_command(h->context, "Foundry_ShopUrl", cmd_shop_url, NULL) ||
        !h->add_lua_command(h->context, "Foundry_MapFines", cmd_map_fines, NULL) ||
        !h->add_lua_command(h->context, "Foundry_FindPlaced", cmd_find_placed, NULL) ||
        !h->add_lua_command(h->context, "Foundry_AiAlert", cmd_ai_alert, NULL) ||
        !h->add_lua_command(h->context, "Foundry_IsEngaging", cmd_is_engaging, NULL) ||
        !h->add_lua_command(h->context, "Foundry_MoveObject", cmd_move_object, NULL) ||
        !h->add_lua_command(h->context, "Foundry_RemoveObject", cmd_remove_object, NULL) ||
        !h->add_lua_command(h->context, "Foundry_ActorTemplate", cmd_actor_template, NULL) ||
        !h->add_lua_command(h->context, "Foundry_SpawnTemplate", cmd_spawn_template, NULL) ||
        !h->add_lua_command(h->context, "Foundry_ActorModel", cmd_actor_model, NULL) ||
        !h->add_lua_command(h->context, "Foundry_MapShop", cmd_map_shop, NULL) ||
        !h->add_lua_command(h->context, "Foundry_MapShopOpen", cmd_map_shop_open, NULL) ||
        !h->add_lua_command(h->context, "Foundry_ShopUnlocked", cmd_shop_unlocked, NULL) ||
        !h->add_lua_command(h->context, "Foundry_ShopRestore", cmd_shop_restore, NULL) ||
        !h->add_lua_command(h->context, "Foundry_FlyLoop", cmd_fly_loop, NULL) ||
        !h->add_lua_command(h->context, "Foundry_PhysicsProbe", cmd_physics_probe, NULL) ||
        !h->add_lua_command(h->context, "Foundry_ProjectilePos", cmd_projectile_pos, NULL) ||
        !h->add_lua_command(h->context, "Foundry_FindRounds", cmd_find_rounds, NULL) ||
        !h->add_lua_command(h->context, "Foundry_IgnoreAttacks", cmd_ignore_attacks, NULL) ||
        !h->add_lua_command(h->context, "Foundry_ScriptValues", cmd_script_values, NULL) ||
        !h->add_lua_command(h->context, "Foundry_CentreWheels", cmd_centre_wheels, NULL) ||
        !h->add_lua_command(h->context, "Foundry_Log", cmd_log, NULL))
        h->log(h->context, "script commands unavailable; Foundry_TrafficZone, Foundry_Range, Foundry_Sample, "
                           "Foundry_Label, Foundry_SetHandling and Foundry_MapText may not exist");
    if (!h->hook_guest(h->context, MF_FN_DataPod_BuildCards, datapod_cards_before, datapod_cards_after, NULL))
        h->log(h->context, "PDA card list hook unavailable (run port-hooks); cards the Deck screen showed before "
                           "may keep the game's texts on a map with its own");
    if (!h->hook_guest(h->context, MF_FN_Briefing_Init, NULL, briefing_init_after, NULL) ||
        !h->hook_guest(h->context, MF_FN_Briefing_Deinit, briefing_deinit_before, NULL, NULL))
        h->log(h->context, "briefing hooks unavailable (run port-hooks); retail HQ briefings aren't measured");
    if (!h->hook_guest(h->context, MF_FN_FrontEnd_BuildPauseMenu, pause_menu_before, NULL, NULL))
        h->log(h->context, "pause menu hook unavailable (run port-hooks); its contract check isn't logged");
    if (!h->hook_guest(h->context, MF_FN_DataPod_DrawTexture, datapod_draw_before, NULL, NULL))
        h->log(h->context, "PDA draw hook unavailable (run port-hooks); a map's own card portraits show once the "
                           "game's have loaded (a moment of the game's first)");
    if (!h->hook_guest(h->context, MF_FN_Utility_SavePlayerData, save_before, save_after, NULL))
        h->log(h->context, "save hook unavailable (run port-hooks); a map's own store (Foundry_MapShop) shows every item");
    else
        shop_hooked = 1;
    if (!h->hook_guest(h->context, MF_FN_Level_Load, level_load_before, NULL, NULL))
        h->log(h->context, "level load hook unavailable (run port-hooks); a map's own text (Foundry_MapText) "
                           "stays after leaving it");
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
