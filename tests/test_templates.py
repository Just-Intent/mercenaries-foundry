import unittest
from unittest import mock

from mercfoundry import templates


def obj(name, layer, props, model="m"):
    return {"name": name, "layer": layer, "model": model, "properties": props}


class TemplatesTests(unittest.TestCase):
    def test_collect_and_tsv(self):
        found = [
            obj("template_b_jeep", "mission_x", [("objectType", "jeep"), ("faction", "nk")]),
            obj("template_a_soldier", "template_humans", [("ObjectType", "human"), ("displayName", "hum.x")]),
            obj("TEMPLATE_B_JEEP", "template_vehicles", [("objectType", "jeep"), ("faction", "allies")]),
        ]
        with mock.patch.object(templates.find, "search", return_value=found) as search:
            rows = templates.collect([])
        search.assert_called_once_with([], name="template_*")
        # One row per name (case ignored), the shared template_* layer wins, sorted by name.
        self.assertEqual([(r["name"], r["layer"]) for r in rows],
                         [("template_a_soldier", "template_humans"), ("TEMPLATE_B_JEEP", "template_vehicles")])
        self.assertEqual((rows[0]["objectType"], rows[0]["displayName"]), ("human", "hum.x"))
        self.assertEqual(rows[1]["faction"], "allies")
        tsv = templates.to_tsv(rows).splitlines()
        self.assertEqual(tsv[0], "name\tlayer\tobjectType\tfaction\tdisplayName\tmodel")
        self.assertEqual(tsv[1], "template_a_soldier\ttemplate_humans\thuman\t\thum.x\tm")


if __name__ == "__main__":
    unittest.main()
