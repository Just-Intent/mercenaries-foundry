import unittest

from mercfoundry import animation, animtable, bones
from mercfoundry.model import Joint


def clip(calf=(0.45, 0.0, 0.0), root=None):
    tracks = [animation.Track("bone_root", [(0, 0, 0, 1)] * 2, translations=root or [(0, 1, 0), (0, 1.1, 0)], dxlt=1e-4),
              animation.Track("bone_l_calf", [(0, 0, 0, 1)] * 2, translation=calf),
              animation.Track("bone_l_foot", [(0, 0, 0, 1)] * 2, translation=(0.48, 0, 0))]
    return animation.Animation("run", 2, 68264.6, tracks)


class BoneTests(unittest.TestCase):
    def test_moved_joints(self):
        old = [Joint("bone_root", (0, 0, 0, 1), (0, 1.178, 0), ""), Joint("bone_l_calf", (0, 0, 0, 1), (0.45, 0, 0), "x")]
        new = [Joint("bone_root", (0, 0, 0, 1), (0, 1.1782, 0), ""),            # under 1 mm: not a move
               Joint("Bone_L_Calf", (0, 0, 0, 1), (0.55, 0, 0), "x"),
               Joint("hp_new", (0, 0, 0, 1), (1, 0, 0), "x")]                    # new: in no clip
        moves = bones.moved_joints(old, new)
        self.assertEqual(list(moves), ["bone_l_calf"])
        self.assertAlmostEqual(moves["bone_l_calf"][0], 0.1)

    def test_retarget_shifts_fixed_and_per_frame(self):
        a = bones.retarget(clip(), {"bone_l_calf": (0.1, 0, 0), "bone_root": (0, 0.2, 0)}, "run_tall")
        self.assertEqual(a.name, "run_tall")
        t = {x.joint: x for x in a.tracks}
        self.assertAlmostEqual(t["bone_l_calf"].translation[0], 0.55)
        self.assertAlmostEqual(t["bone_root"].translations[1][1], 1.3)
        self.assertEqual(t["bone_l_foot"].translation, (0.48, 0, 0))
        # a longer bone than the clip's scale holds: a coarser scale, and it encodes
        self.assertLess(a.scale, 68264.6)
        back = animation.decode(animation.encode(a))
        self.assertAlmostEqual({x.joint: x for x in back.tracks}["bone_l_calf"].translation[0], 0.55, places=3)
        self.assertIsNone(bones.retarget(clip(), {"bone_r_calf": (0.1, 0, 0)}, "x"))   # not in this clip

    def test_tables_renamed_and_chained(self):
        doc = {"kind": "animtable", "name": "Player_American", "fallback": "player_common", "variants": ["Idle1", "idle2"],
               "sets": [{"name": "default", "entries": [{"handle": "lb_run", "anim": "Run", "priority": 40, "flags": "0x0"},
                                                        {"handle": "idle", "variants": {"start": 0, "count": 2}}]}]}
        common = {"kind": "animtable", "name": "player_common", "variants": [], "sets": []}
        tables = {"player_american": (None, None, doc), "player_common": (None, None, common)}
        self.assertEqual(bones.table_chain(tables, "Player_American"), ["player_american", "player_common"])
        names = {"run": "run_m", "idle1": "idle1_m"}
        out = bones.rename_clips(doc, names, {"player_american": "player_american_m", "player_common": "player_common_m"})
        self.assertEqual((out["name"], out["fallback"]), ("player_american_m", "player_common_m"))
        self.assertEqual(out["variants"], ["idle1_m", "idle2"])
        self.assertEqual(out["sets"][0]["entries"][0]["anim"], "run_m")
        self.assertEqual(doc["sets"][0]["entries"][0]["anim"], "Run")          # the original is untouched
        self.assertEqual(animtable.to_json(animtable.from_json(out))["variants"], ["idle1_m", "idle2"])


if __name__ == "__main__":
    unittest.main()
