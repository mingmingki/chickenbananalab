"""Frames drawing several plans under their own captions (two towers, a roof and its penthouse
roof) are split into one plan each before the pictures, the 3D and the takeoff."""
import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from core import quantity_floor_views as fv
from core.test_quantity_sheet_identity import form_doc, xref_host

SCALE = 100.0


def wall_box(msp, x0, y0, x1, y1, t=200, layer="0_STR_CONC"):
    for inset in (0, t):
        a, b, c, d = x0 + inset, y0 + inset, x1 - inset, y1 - inset
        msp.add_lwpolyline([(a, b), (c, b), (c, d), (a, d)], close=True, dxfattribs={"layer": layer})


def drawing(title, captions, boxes, number="S-115"):
    doc = xref_host([(number, title)], scale=SCALE)
    msp = doc.modelspace()
    for box in boxes:
        wall_box(msp, *box)
    for text, x, y in captions:
        msp.add_text(text, dxfattribs={"insert": (x, y), "height": 450, "layer": "0_STR_TEXT"})
    return doc


def run(doc, name="S-115 동 구조평면도.dxf"):
    tmp = tempfile.TemporaryDirectory()
    folder = Path(tmp.name)
    files = []
    for file, item in ((name, doc), ("XR-FORM-A(A3).dxf", form_doc())):
        path = folder / file
        item.saveas(path)
        files.append({"name": file, "dxf": str(path), "raw": str(path)})
    manifest, out = folder / "manifest.json", folder / "out.json"
    manifest.write_text(json.dumps({"files": files, "plans": [name]}), encoding="utf-8")
    fv.main(str(manifest), str(out))
    result = json.loads(out.read_text(encoding="utf-8"))
    tmp.cleanup()
    return result


TWO_TOWERS = [(5000, 15000, 25000, 30000), (45000, 15000, 65000, 30000)]


class FrameSplitTests(SimpleTestCase):
    def test_two_towers_in_one_frame_become_two_buildings(self):
        doc = drawing("101동 3~25층 구조평면도", [("101동 3~25층 구조평면도", 8000, 12000), ("102동 3~25층 구조평면도", 48000, 12000)],
                      TWO_TOWERS)
        result = run(doc)
        buildings = {b["name"]: b for b in result["model"]["buildings"]}
        self.assertEqual(set(buildings), {"101동", "102동"})
        for name in ("101동", "102동"):
            levels = buildings[name]["levels"]
            self.assertEqual([l["key"] for l in levels][:2], ["3", "4"])
            plan = buildings[name]["plans"][0]
            self.assertAlmostEqual(plan["slab_area"], 20 * 15, delta=20)  # one tower each, not both
        views = [v for v in result["views"] if v["discipline"] == "struct"]
        self.assertEqual(sorted(v["building"] for v in views), ["101동", "102동"])
        self.assertEqual(len({v["plan_id"] for v in views}), 2)

    def test_a_roof_and_its_penthouse_roof_in_one_frame_stack(self):
        doc = drawing("101동 지붕, 옥탑지붕 구조평면도",
                      [("지붕층 구조평면도", 8000, 12000), ("옥탑지붕 구조평면도", 48000, 12000)],
                      [(5000, 15000, 25000, 30000), (45000, 15000, 52000, 21000)])
        levels = run(doc)["model"]["buildings"][0]["levels"]
        self.assertEqual([l["key"] for l in levels], ["R", "PHR"])

    def test_one_caption_or_none_leaves_the_frame_whole(self):
        doc = drawing("101동 3~25층 구조평면도", [("101동 3~25층 구조평면도", 8000, 12000)], TWO_TOWERS)
        buildings = run(doc)["model"]["buildings"]
        self.assertEqual([b["name"] for b in buildings], ["101동"])
        self.assertEqual(len(buildings[0]["plans"]), 1)

    def test_notes_that_refer_to_another_plan_are_not_captions(self):
        doc = drawing("101동 3~25층 구조평면도", [("101동 3~25층 구조평면도", 8000, 12000),
                                              ("주차장 부분은 지하1층 주차장 구조평면도 참조", 48000, 12000)], TWO_TOWERS)
        self.assertEqual([b["name"] for b in run(doc)["model"]["buildings"]], ["101동"])
