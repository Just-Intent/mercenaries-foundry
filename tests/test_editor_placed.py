import json
import os
import tempfile
import unittest
from pathlib import Path

from mercfoundry.editor import scene as scene_mod
from mercfoundry.editor.scene import Scene

IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1]


def turned(yaw):
    from mercfoundry.encounters import _rotation
    return scene_mod.to_three(_rotation(yaw))


class PlacedSaveTests(unittest.TestCase):
    """Saving placed objects keeps what the editor doesn't edit, and leaves unchanged layers alone."""

    def scene(self, root):
        sc = Scene.__new__(Scene)
        sc.mod = root
        sc.archives = []
        sc.all_objects = lambda: []
        sc.names = lambda: {}
        sc.editor_layer_file = lambda m: "mine_isle.json"
        sc._require = lambda v: None
        return sc

    def test_layer_keys_kept_and_unchanged_layers_not_written(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "encounters").mkdir()
            p = root / "encounters" / "gis.json"
            layer = {"map": "ISLE", "load": "map", "unnamed": True, "copy_loads": ["x"],
                     "boundaries": {"b": {"points": [[0, 0, 0], [1, 0, 0], [0, 0, 1]]}},
                     "objects": [{"from": "model:hut", "name": "gis_1", "position": [1.5, 2.25, 3.125], "yaw": -45.0,
                                  "scale": 2},
                                 {"from": "model:hut", "name": "gis_2", "position": [9, 0, 9], "yaw": 0}]}
            p.write_text(json.dumps(layer, indent=1) + "\n", encoding="utf-8")
            sc = self.scene(root)
            same = [{"from": "model:hut", "name": "gis_1", "position": [1.5, 2.25, 3.125], "rotation": turned(-45.0)},
                    {"from": "model:hut", "name": "gis_2", "position": [9, 0, 9], "rotation": turned(0)}]
            os.utime(p, (1, 1))
            sc.save_placed("isle", {"gis.json": same})
            self.assertEqual(p.stat().st_mtime, 1)                      # nothing changed: not written
            moved = [dict(same[0], position=[2, 2.25, 3.125]), same[1]]
            sc.save_placed("isle", {"gis.json": moved})
            doc = json.loads(p.read_text(encoding="utf-8"))
            self.assertTrue(doc["unnamed"])
            self.assertEqual(doc["copy_loads"], ["x"])
            self.assertIn("boundaries", doc)
            self.assertEqual(doc["objects"][0]["scale"], 2)              # an object's own keys stay
            self.assertEqual(doc["objects"][0]["position"], [2.0, 2.25, 3.125])
            self.assertEqual(doc["objects"][1], {"from": "model:hut", "name": "gis_2", "position": [9, 0, 9], "yaw": 0})


if __name__ == "__main__":
    unittest.main()
