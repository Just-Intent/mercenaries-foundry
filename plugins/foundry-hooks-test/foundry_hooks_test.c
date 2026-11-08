/* Foundry hooks test: plugin hooks on game functions, using the function map.
 *
 *   Player_SetMoney (before): doubles every money gain.
 *   HashName (before): for "foundry_skip_test", skips the original and
 *       returns 0x12345678; a guest call checks this, and that other
 *       strings still hash normally.
 *   Spawner_SpawnTemplate (after): logs the game's spawns.
 * Results go to plugin-settings/plugins.log.
 */
#define MERC_PLUGIN_IMPLEMENTATION
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "mercenaries_plugin.h"
#include "mercfoundry_functions.h"

static const MercPluginHost *host;
static double gameplay_seconds;
static int checked;
static unsigned spawns, money_changes;

static void logf_(const char *fmt, ...)
{
    char text[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof(text), fmt, ap);
    va_end(ap);
    host->log(host->context, text);
}

static uint32_t arg(const MercHookContext *c, unsigned n)
{
    uint32_t v = 0;
    host->read_guest(host->context, c->esp + 4 + 4 * n, &v, 4);
    return v;
}

static void MERC_PLUGIN_CALL money_before(void *ud, MercHookContext *c)
{
    (void)ud;
    float current = 0, next;
    uint32_t bits = arg(c, 0);
    memcpy(&next, &bits, 4);
    host->read_guest(host->context, c->ecx + MF_OFF_Actor_money, &current, 4);
    if (next > current) {
        float doubled = current + 2.0f * (next - current);
        memcpy(&bits, &doubled, 4);
        host->write_guest(host->context, c->esp + 4, &bits, 4);
        if (money_changes++ < 20)
            logf_("Player_SetMoney: %.0f -> %.0f, doubled to %.0f", current, next, doubled);
    }
}

static void MERC_PLUGIN_CALL hash_before(void *ud, MercHookContext *c)
{
    (void)ud;
    char text[20] = {0};
    uint32_t p = arg(c, 0);
    if (!p || !host->read_guest(host->context, p, text, sizeof(text) - 1))
        return;
    if (strcmp(text, "foundry_skip_test") == 0) {
        c->skip = 1;
        c->eax = 0x12345678u;
        c->pop_bytes = 0; /* __cdecl */
    }
}

static void MERC_PLUGIN_CALL spawn_after(void *ud, MercHookContext *c)
{
    (void)ud;
    if (spawns++ < 15)
        logf_("Spawner_SpawnTemplate(template 0x%08X) -> 0x%08X", arg(c, 0), c->eax);
}

static uint32_t hash_via_game(const char *s)
{
    uint32_t scratch, size;
    MercGuestCall call;
    if (!host->guest_scratch(host->context, &scratch, &size))
        return 0;
    host->write_guest(host->context, scratch, s, strlen(s) + 1);
    memset(&call, 0, sizeof(call));
    call.struct_size = sizeof(call);
    call.address = MF_FN_HashName;
    call.arg_count = 1;
    call.args[0] = scratch;
    if (!host->call_guest(host->context, &call)) {
        logf_("call refused: %s", call.error);
        return 0;
    }
    return call.eax;
}

static int MERC_PLUGIN_CALL on_update(void *ud, const MercPluginUpdate *u)
{
    (void)ud;
    if (checked)
        return 1;
    if (!host->in_gameplay(host->context)) {
        gameplay_seconds = 0;
        return 1;
    }
    gameplay_seconds += u->wall_delta_seconds;
    if (gameplay_seconds < 7.0)
        return 1;
    checked = 1;
    uint32_t skipped = hash_via_game("foundry_skip_test");
    uint32_t normal = hash_via_game("player0");
    logf_("skip test: HashName(\"foundry_skip_test\") = 0x%08X (expected 0x12345678): %s",
          skipped, skipped == 0x12345678u ? "OK" : "FAILED");
    logf_("pass-through: HashName(\"player0\") = 0x%08X (expected 0x660E4490): %s",
          normal, normal == 0x660E4490u ? "OK" : "FAILED");
    logf_("so far: %u spawns seen, %u money gains doubled", spawns, money_changes);
    return 1;
}

MERC_PLUGIN_EXPORT int MERC_PLUGIN_CALL MercenariesPlugin_Init(
    const MercPluginHost *h, MercPlugin *plugin, char *error, uint32_t error_size)
{
    if (!MERC_PLUGIN_HOST_HAS(h, hook_guest)) {
        snprintf(error, error_size, "This game build has no plugin hooks (Foundry patch 0002).");
        return 0;
    }
    host = h;
    if (!h->hook_guest(h->context, MF_FN_Player_SetMoney, money_before, NULL, NULL) ||
        !h->hook_guest(h->context, MF_FN_HashName, hash_before, NULL, NULL) ||
        !h->hook_guest(h->context, MF_FN_Spawner_SpawnTemplate, NULL, spawn_after, NULL)) {
        snprintf(error, error_size, "Couldn't hook; run `python -m mercfoundry port-hooks` and rebuild the port.");
        return 0;
    }
    plugin->on_update = on_update;
    return 1;
}
