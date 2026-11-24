import unittest

from mercfoundry import shop

GAME_INI = ("[ShopItem]\r\nTemplate_object_name\ttemplate_support_p_surgicalstrike\r\nCategory\t\tWeapon\r\n"
            "Texture\t\t\tshop_surgicalstrike\r\nDefaultPrice\t\t50000\r\n\r\n"
            "[ShopItem]\r\nTemplate_object_name\ttemplate_support_deliverStolenCivCar\r\nCategory\t\tVehicle\r\n"
            "Texture\t\t\tshop_civcar\r\nDefaultPrice\t\t5000\r\n")
PROPS = {
    "template_support_p_surgicalstrike": {"ammo": "template_sam_p_surgicalstrike", "hudtexturename": "SupportIconX1Y2"},
    "template_sam_p_surgicalstrike": {"airstrikename": "template_allies_support_tornado", "monetarycost": "50000"},
    "template_support_deliverstolenciv car".replace(" ", ""): {"ammo": "template_sam_deliverStolenCivCar"},
    "template_sam_deliverstolencivcar": {"deliveryhelicoptertemplatename": "template_mafia_cargocopter",
                                         "spawnobjecttemplatename": "template_civ_80civic_empty"},
}


def props_of(t):
    return PROPS.get(t.lower())


def doc(*items):
    return {"shop": {"items": list(items)}}


class ShopTest(unittest.TestCase):
    def items(self, *its):
        return shop.items("maps/gaza.json", "gaza", doc(*its))

    def test_names_and_unlocks(self):
        it = self.items({"id": "hermes", "from": "template_support_p_surgicalstrike", "name": "Hermes",
                         "price": 90000, "unlock": "intel spades 80"})[0]
        self.assertEqual(it["weapon"], "template_support_gaza_hermes")
        self.assertEqual(it["ammo"], "template_sam_gaza_hermes")
        self.assertEqual(it["unlock"], ("intel", "spades", 80))
        self.assertEqual(shop.parse_unlock("w", "after sw_mafia20"), ("after", "sw_mafia20"))
        self.assertEqual(shop.parse_unlock("w", None), ("start",))
        with self.assertRaises(ValueError):
            shop.parse_unlock("w", "when it rains")
        with self.assertRaises(ValueError):
            self.items({"id": "a b", "from": "template_x", "name": "x", "price": 1})
        with self.assertRaises(ValueError):
            self.items({"id": "a", "from": "template_x", "name": "x", "price": "lots"})

    def test_template_entries(self):
        its = self.items({"id": "hermes", "from": "template_support_p_surgicalstrike", "name": "Hermes",
                          "price": 90000, "aircraft": "template_gaza_hermes"},
                         {"id": "taxi", "from": "template_support_deliverStolenCivCar", "name": "Taxi",
                          "price": 15000, "load": "template_gaza_taxi_empty"})
        known = {"template_gaza_hermes", "template_gaza_taxi_empty"}
        out = shop.template_entries(its, shop.catalogue_blocks(GAME_INI), props_of, lambda t: t.lower() in known)
        ammo, weapon = out[0], out[1]
        self.assertEqual(ammo, {"template": "template_sam_p_surgicalstrike", "as": "template_sam_gaza_hermes",
                                "set": {"MonetaryCost": "90000", "AirstrikeName": "template_gaza_hermes"}})
        self.assertEqual(weapon["set"]["Ammo"], "template_sam_gaza_hermes")
        self.assertEqual(weapon["set"]["HudTextureName"], "SupportIconX1Y2")
        self.assertEqual(out[2]["set"]["spawnObjectTemplateName"], "template_gaza_taxi_empty")
        self.assertEqual(its[1]["category"], "Vehicle")
        self.assertEqual(its[1]["picture"], "shop_civcar")
        # an airstrike's aircraft on a delivery, a missing load, a non-shop "from"
        bad = self.items({"id": "x", "from": "template_support_deliverStolenCivCar", "name": "X", "price": 1,
                          "aircraft": "template_gaza_hermes"})
        with self.assertRaises(ValueError):
            shop.template_entries(bad, shop.catalogue_blocks(GAME_INI), props_of, lambda t: True)
        bad = self.items({"id": "x", "from": "template_support_deliverStolenCivCar", "name": "X", "price": 1,
                          "load": "template_nothing"})
        with self.assertRaises(ValueError):
            shop.template_entries(bad, shop.catalogue_blocks(GAME_INI), props_of, lambda t: False)
        bad = self.items({"id": "x", "from": "template_support_p_carpetbomb", "name": "X", "price": 1})
        with self.assertRaises(ValueError):
            shop.template_entries(bad, shop.catalogue_blocks(GAME_INI), props_of, lambda t: True)

    def test_catalogue_strings_lua(self):
        its = self.items({"id": "hermes", "from": "template_support_p_surgicalstrike", "name": "Hermes",
                          "short": "A drone.", "price": 90000, "unlock": "after sw_allies21"})
        shop.template_entries(its, shop.catalogue_blocks(GAME_INI), props_of, lambda t: True)
        text = shop.catalogue_text(GAME_INI, its)
        blocks = shop.catalogue_blocks(text)
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[2]["Template_object_name"], "template_support_gaza_hermes")
        self.assertEqual(blocks[2]["DefaultPrice"], "90000")
        self.assertIn("\r\n", text)
        s = shop.strings(its)
        self.assertEqual(s["support.gaza.hermes"], "Hermes")
        self.assertEqual(s["shop.gaza.hermes.short"], "A drone.")
        self.assertEqual(s["shop.gaza.hermes.long"], "A drone.")
        lua = shop.lua("foundry_gaza_", "gaza", its)
        self.assertIn("Foundry_MapShop('template_support_gaza_hermes')", lua)
        self.assertIn("Utility_ReadNumberFromScribbleMemory('foundry_done_sw_allies21')", lua)
        with self.assertRaises(ValueError):
            shop.catalogue_text(GAME_INI * 64, its)
        # the game's lock is left alone unless "open": then Foundry Console opens it here only
        self.assertNotIn("Shop_SetShopLocked", lua)
        self.assertNotIn("Foundry_MapShopOpen", lua)
        self.assertIn("Foundry_MapShopOpen()", shop.lua("foundry_gaza_", "gaza", its, True))
        self.assertTrue(shop.is_open("w", {"shop": {"open": True, "items": []}}))
        self.assertFalse(shop.is_open("w", {"shop": {"items": []}}))
        with self.assertRaises(ValueError):
            shop.is_open("w", {"shop": {"open": "yes", "items": []}})

    def test_load_list(self):
        text = "[model]\r\nload a\r\n\r\n[texture]\r\nload shop_x\r\n"
        new = shop.add_textures_to_list(text, ["shop_x", "shop_y", "shop_y"])
        self.assertEqual(new.lower().count("load shop_y"), 1)
        self.assertIsNone(shop.add_textures_to_list(new, ["shop_y"]))


if __name__ == "__main__":
    unittest.main()
