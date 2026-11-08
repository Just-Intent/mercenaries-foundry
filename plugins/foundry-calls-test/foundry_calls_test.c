/* Foundry calls test: exercises plugin guest calls against the function map.
 *
 * Once normal gameplay has run for a few seconds it, one step at a time:
 *   1. hashes "player0" with the game's HashName (string argument, eax result)
 *   2. finds the player with World_FindActorByName
 *   3. gives $1,000,000 through Player_SetMoney (__thiscall, float argument)
 *   4. parses "12.5" with atof (x87 result)
 *   5. spawns a Humvee 10 m ahead with Spawner_SpawnTemplate
 * and logs each result to plugin-settings/plugins.log.
 */
#define MERC_PLUGIN_IMPLEMENTATION
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <math.h>
#include "mercenaries_plugin.h"
#include "mercfoundry_functions.h"

/* Template property lookup: (template hash, property hash) -> value, or 0 when
 * the template isn't loaded. Used by the port's dev spawner as an availability
 * check before spawning. */
#define TEMPLATE_GET_PROPERTY 0x001ED340u

typedef struct State {
    const MercPluginHost *host;
    double gameplay_seconds;
    int done;
} State;

static State state;

static void logf_(const char *fmt, ...)
{
    char text[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof(text), fmt, ap);
    va_end(ap);
    state.host->log(state.host->context, text);
}

static int call(uint32_t address, uint32_t this_ptr, unsigned n, const uint32_t *args, MercGuestCall *out)
{
    memset(out, 0, sizeof(*out));
    out->struct_size = sizeof(*out);
    out->address = address;
    out->this_ptr = this_ptr;
    out->arg_count = n;
    if (n) memcpy(out->args, args, n * sizeof(uint32_t));
    if (!state.host->call_guest(state.host->context, out)) {
        logf_("call 0x%08X refused: %s", address, out->error);
        return 0;
    }
    return 1;
}

static uint32_t float_bits(float f) { uint32_t u; memcpy(&u, &f, 4); return u; }

static uint32_t put_string(uint32_t *cursor, const char *s)
{
    uint32_t at = *cursor;
    size_t n = strlen(s) + 1;
    state.host->write_guest(state.host->context, at, s, n);
    *cursor += (uint32_t)((n + 15) & ~15u);
    return at;
}

static void run_tests(void)
{
    MercGuestCall c;
    uint32_t scratch, size, cursor;
    if (!state.host->guest_scratch(state.host->context, &scratch, &size)) {
        logf_("no scratch memory");
        return;
    }
    cursor = scratch;
    logf_("scratch at 0x%08X, %u bytes", scratch, size);

    /* 1. HashName("player0") */
    uint32_t name = put_string(&cursor, "player0");
    if (!call(MF_FN_HashName, 0, 1, &name, &c)) return;
    logf_("1. HashName(\"player0\") = 0x%08X (expected 0x660E4490): %s",
          c.eax, c.eax == 0x660E4490u ? "OK" : "MISMATCH");
    uint32_t player_hash = c.eax;

    /* 2. World_FindActorByName */
    if (!call(MF_FN_World_FindActorByName, 0, 1, &player_hash, &c)) return;
    uint32_t actor = c.eax;
    logf_("2. World_FindActorByName(player0) = 0x%08X: %s", actor, actor ? "OK" : "not found");
    if (!actor) return;

    /* 3. Player_SetMoney(actor, money + 1,000,000) */
    float before = 0, after = 0;
    state.host->read_guest(state.host->context, actor + MF_OFF_Actor_money, &before, 4);
    uint32_t money = float_bits(before + 1000000.0f);
    if (!call(MF_FN_Player_SetMoney, actor, 1, &money, &c)) return;
    state.host->read_guest(state.host->context, actor + MF_OFF_Actor_money, &after, 4);
    logf_("3. Player_SetMoney: money %.0f -> %.0f: %s", before, after,
          fabsf(after - before - 1000000.0f) < 1.0f ? "OK" : "MISMATCH");

    /* 4. atof("12.5") returns on the x87 stack */
    uint32_t text = put_string(&cursor, "12.5");
    if (!call(MF_FN_atof, 0, 1, &text, &c)) return;
    logf_("4. atof(\"12.5\") = %g: %s", c.st0, c.st0 == 12.5 ? "OK" : "MISMATCH");

    /* 5. Spawn a Humvee 10 m ahead */
    float m[16];
    state.host->read_guest(state.host->context, actor + MF_OFF_Actor_transform, m, sizeof(m));
    logf_("5. player transform: right (%.2f %.2f %.2f) up (%.2f %.2f %.2f) forward (%.2f %.2f %.2f) at (%.1f %.1f %.1f)",
          m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10], m[12], m[13], m[14]);
    if (!isfinite(m[12]) || fabsf(m[12]) > 20000 || fabsf(m[13]) > 20000 || fabsf(m[14]) > 20000 ||
        fabsf(m[8] * m[8] + m[9] * m[9] + m[10] * m[10] - 1.0f) > 0.05f) {
        logf_("5. transform doesn't look like a placement; not spawning");
        return;
    }
    /* Vehicle templates come from the template_vehicles layer, named
     * template_<faction>_<vehicle>; "_empty" variants have no crew. */
    static const char *templates[] = {"template_allies_humvee_empty", "template_sk_humveemg_empty",
                                      "template_allies_humvee"};
    uint32_t name_prop_hash;
    uint32_t prop = put_string(&cursor, "name");
    if (!call(MF_FN_HashName, 0, 1, &prop, &c)) return;
    name_prop_hash = c.eax;
    for (unsigned i = 0; i < sizeof(templates) / sizeof(templates[0]); i++) {
        uint32_t t = put_string(&cursor, templates[i]);
        if (!call(MF_FN_HashName, 0, 1, &t, &c)) return;
        uint32_t template_hash = c.eax;
        uint32_t check[2] = {template_hash, name_prop_hash};
        if (!call(TEMPLATE_GET_PROPERTY, 0, 2, check, &c)) return;
        logf_("5. template %s (0x%08X): %s", templates[i], template_hash, c.eax ? "loaded" : "not loaded");
        if (!c.eax) continue;
        float spawn[16];
        memcpy(spawn, m, sizeof(spawn));
        spawn[12] = m[12] + m[8] * 10.0f;
        spawn[13] = m[13] + 2.0f;
        spawn[14] = m[14] + m[10] * 10.0f;
        spawn[3] = spawn[7] = spawn[11] = 0.0f;
        spawn[15] = 1.0f;
        uint32_t matrix = cursor;
        cursor += sizeof(spawn);
        state.host->write_guest(state.host->context, matrix, spawn, sizeof(spawn));
        uint32_t spawn_name = put_string(&cursor, "foundry_plugin_humvee");
        uint32_t args[5] = {template_hash, spawn_name, matrix, 0, 0};
        if (!call(MF_FN_Spawner_SpawnTemplate, MF_VAR_g_Spawner, 5, args, &c)) return;
        logf_("5. Spawner_SpawnTemplate(%s) = 0x%08X at (%.1f %.1f %.1f): %s", templates[i], c.eax,
              spawn[12], spawn[13], spawn[14], c.eax ? "OK" : "failed");
        return;
    }
    logf_("5. no Humvee template is loaded here; skipped the spawn");
}

static int MERC_PLUGIN_CALL on_update(void *userdata, const MercPluginUpdate *update)
{
    (void)userdata;
    if (state.done) return 1;
    if (!state.host->in_gameplay(state.host->context)) {
        state.gameplay_seconds = 0;
        return 1;
    }
    state.gameplay_seconds += update->wall_delta_seconds;
    if (state.gameplay_seconds < 5.0) return 1;
    state.done = 1;
    logf_("in gameplay; running guest call tests");
    run_tests();
    logf_("tests finished");
    return 1;
}

MERC_PLUGIN_EXPORT int MERC_PLUGIN_CALL MercenariesPlugin_Init(
    const MercPluginHost *host, MercPlugin *plugin, char *error, uint32_t error_size)
{
    if (!MERC_PLUGIN_HOST_HAS(host, call_guest) || !MERC_PLUGIN_HOST_HAS(host, guest_scratch) ||
        !MERC_PLUGIN_HOST_HAS(host, in_gameplay)) {
        snprintf(error, error_size, "This game build has no plugin guest calls (Foundry patch).");
        return 0;
    }
    state.host = host;
    plugin->on_update = on_update;
    host->log(host->context, "loaded; waiting for gameplay");
    return 1;
}
