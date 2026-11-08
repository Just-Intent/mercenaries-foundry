import unittest
from unittest import mock

from mercfoundry import vehicles


def obj(name, model, kind, **physics):
    props = [["objecttype", kind], ["displayName", "veh." + model]]
    props += [[k, v] for k, v in physics.items()]
    return {"name": name, "model": model, "layer": "l", "properties": props}


OBJECTS = [
    obj("player_humvee", "humvee", "jeep", physicsMass="3500", physicsenginetorque="900"),
    obj("template_humvee", "humvee", "jeep", physicsMass="3500", physicsenginetorque="850"),
    obj("crate1", "crate", "crate", physicsMass="50"),
    obj("template_heli", "heli", "helicopter", physicsclimbspeed="20"),
]


class VehicleTests(unittest.TestCase):
    def test_collect(self):
        with mock.patch("mercfoundry.find.objects", return_value=OBJECTS):
            found = vehicles.collect([])
        self.assertEqual(list(found), ["heli", "humvee"])      # crates aren't vehicles
        humvee = found["humvee"]
        self.assertEqual(humvee["objects"], 2)
        self.assertEqual(humvee["templates"], ["template_humvee"])
        self.assertEqual(humvee["handling"], {"physicsMass": "3500", "physicsenginetorque": "850"})  # the template's
        self.assertEqual(humvee["varies"], ["physicsenginetorque"])

    def test_rule(self):
        with mock.patch("mercfoundry.find.objects", return_value=OBJECTS):
            found = vehicles.collect([])
        self.assertEqual(vehicles.rule("heli", found["heli"]),
                         {"model": "heli", "set": {"physicsclimbspeed": "20"}})


if __name__ == "__main__":
    unittest.main()
