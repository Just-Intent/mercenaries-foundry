/* Foundry Lua test: script commands added by a plugin.
 *
 *   Foundry_Add(a, b)          -> a + b
 *   Foundry_GiveMoney(amount)  -> the player's money after adding amount
 *   Foundry_Log(text)          -> writes text to plugin-settings/plugins.log
 *
 * Arguments are read and results pushed with the game's own Lua functions
 * (addresses from the function map), through call_guest.
 */
#define MERC_PLUGIN_IMPLEMENTATION
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "mercenaries_plugin.h"
#include "mercfoundry_functions.h"

static const MercPluginHost *host;

static void logf_(const char *fmt, ...)
{
    char text[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof(text), fmt, ap);
    va_end(ap);
    host->log(host->context, text);
}

static int guest(uint32_t fn, uint32_t self, unsigned n, const uint32_t *args, MercGuestCall *c)
{
    memset(c, 0, sizeof(*c));
    c->struct_size = sizeof(*c);
    c->address = fn;
    c->this_ptr = self;
    c->arg_count = n;
    memcpy(c->args, args, n * 4);
    if (!host->call_guest(host->context, c)) {
        logf_("call 0x%08X refused: %s", fn, c->error);
        return 0;
    }
    return 1;
}

static double arg_number(uint32_t L, int n)
{
    MercGuestCall c;
    uint32_t args[2] = {L, (uint32_t)n};
    return guest(MF_FN_Lua_ArgNumber, 0, 2, args, &c) ? c.st0 : 0.0;
}

static void push_number(uint32_t L, double v)
{
    MercGuestCall c;
    uint32_t words[2], args[3];
    memcpy(words, &v, 8);
    args[0] = L;
    args[1] = words[0];
    args[2] = words[1];
    guest(MF_FN_lua_pushnumber, 0, 3, args, &c);
}

static int MERC_PLUGIN_CALL cmd_add(void *ud, uint32_t L)
{
    (void)ud;
    push_number(L, arg_number(L, 1) + arg_number(L, 2));
    return 1;
}

static int MERC_PLUGIN_CALL cmd_give_money(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    float amount = (float)arg_number(L, 1), money = 0;
    uint32_t hash = 0x660E4490u; /* HashName("player0") */
    if (guest(MF_FN_World_FindActorByName, 0, 1, &hash, &c) && c.eax) {
        uint32_t player = c.eax, bits;
        host->read_guest(host->context, player + MF_OFF_Actor_money, &money, 4);
        money += amount;
        memcpy(&bits, &money, 4);
        guest(MF_FN_Player_SetMoney, player, 1, &bits, &c);
        host->read_guest(host->context, player + MF_OFF_Actor_money, &money, 4);
    }
    push_number(L, money);
    return 1;
}

static int MERC_PLUGIN_CALL cmd_log(void *ud, uint32_t L)
{
    (void)ud;
    MercGuestCall c;
    char text[201] = {0};
    uint32_t args[2] = {L, 1};
    if (guest(MF_FN_Lua_ArgString, 0, 2, args, &c) && c.eax) {
        for (unsigned i = 0; i < sizeof(text) - 1; i++) {
            if (!host->read_guest(host->context, c.eax + i, &text[i], 1) || !text[i])
                break;
        }
        logf_("script says: %s", text);
    }
    return 0;
}

MERC_PLUGIN_EXPORT int MERC_PLUGIN_CALL MercenariesPlugin_Init(
    const MercPluginHost *h, MercPlugin *plugin, char *error, uint32_t error_size)
{
    (void)plugin;
    if (!MERC_PLUGIN_HOST_HAS(h, add_lua_command)) {
        snprintf(error, error_size, "This game build can't add script commands (Foundry patch 0003).");
        return 0;
    }
    host = h;
    if (!h->add_lua_command(h->context, "Foundry_Add", cmd_add, NULL) ||
        !h->add_lua_command(h->context, "Foundry_GiveMoney", cmd_give_money, NULL) ||
        !h->add_lua_command(h->context, "Foundry_Log", cmd_log, NULL)) {
        snprintf(error, error_size, "Couldn't add script commands; run `python -m mercfoundry port-hooks` and rebuild the port.");
        return 0;
    }
    return 1;
}
