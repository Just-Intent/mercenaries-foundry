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
    load_names();
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
