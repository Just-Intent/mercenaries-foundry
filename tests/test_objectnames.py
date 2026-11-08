import unittest
from unittest import mock

from mercfoundry import templates


class ObjectNamesTests(unittest.TestCase):
    def test_distinct_names_sorted(self):
        objs = [{"name": "loc_b"}, {"name": ""}, {"name": "LOC_B"}, {"name": "loc_a"}, {"name": None}]
        with mock.patch.object(templates.find, "objects", return_value=iter(objs)):
            self.assertEqual(templates.object_names([]), ["loc_a", "loc_b"])


if __name__ == "__main__":
    unittest.main()
