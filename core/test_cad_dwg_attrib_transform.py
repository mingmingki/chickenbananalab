import io
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import SimpleTestCase

from . import views as core_views
from .test_cad_dwg_text_validation import EXECUTABLE, _run_writer
from .test_oda_review import find_oda

R90 = math.pi / 2
# ezdxf + ODA (review only): block TAGBLK (circle r10, ATTDEF NO centred, NAME
# left), INSERT at (100, 100) with NO="A-101" centred (insertion point (85, 85),
# alignment point (100, 85)) and NAME="LOBBY" at (88, 112); a centred TEXT
# "CENTER" (135, 60) / (150, 60); an MTEXT "NOTE" at the origin.
FIXTURE = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad" / "attrib_justified_ac1032.dwg"


def _r(point):
    return (round(point[0], 4), round(point[1], 4))


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadDwgAttribTransformTests(SimpleTestCase):
    """Rotating, scaling, mirroring and copying a block with attributes.

    The save refused these edits ("회전·크기를 바꾼 속성"): ATTRIBs are separate
    entities in world space and the writer only moved them.  The editor now
    sends where it shows each attribute; the writer places it there, keeping
    the alignment point of justified text at the same spot of the text.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-attrib-transform-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.source = FIXTURE
        msp = self.read(self.source)
        self.insert = msp.query("INSERT")[0]
        self.attribs = {a.dxf.tag: a for a in self.insert.attribs}
        self.text = msp.query("TEXT")[0]
        self.mtext = msp.query("MTEXT")[0]

    def read(self, dwg):
        import ezdxf

        out = self.tmp / (dwg.stem + ".dxf")
        run = subprocess.run([str(EXECUTABLE), "--dxf", str(dwg), str(out)], capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        return ezdxf.read(io.StringIO(out.read_bytes().decode("cp949", errors="replace"))).modelspace()

    def save(self, ops):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        output = self.tmp / "out.dwg"
        report = _run_writer([self.source, output, "AC1018", ops_path])
        core_views._cbl_free_dwg_save_local_validate_v1(self.source, output, None, ops, report)
        return self.read(output), report

    def refused(self, ops):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": ops}), encoding="utf-8")
        run = subprocess.run([str(EXECUTABLE), str(self.source), str(self.tmp / "x.dwg"), "AC1018", str(ops_path)], capture_output=True, timeout=300)
        self.assertNotEqual(run.returncode, 0)
        return core_views._cbl_free_dwg_writer_error_message_v1(run.stderr.decode("utf-8", "replace"))

    def placement(self, tag, insert, rotation, height, **extra):
        value = {"handle": self.attribs[tag].dxf.handle, "tag": tag, "insert": [insert[0], insert[1], 0], "rotation": rotation, "height": height}
        value.update(extra)
        return value

    def insert_op(self, insert, rotation, scale, attributes, **extra):
        op = {"type": "update", "handle": self.insert.dxf.handle, "sourceHandle": self.insert.dxf.handle, "entity": "INSERT",
              "blockName": "TAGBLK", "layer": "0", "insert": [insert[0], insert[1], 0], "rotation": rotation,
              "scale": list(scale), "attributes": attributes}
        op.update(extra)
        return op

    def placed(self, msp):
        insert = next(i for i in msp.query("INSERT") if i.dxf.handle == self.insert.dxf.handle)
        return insert, {a.dxf.tag: a for a in insert.attribs}

    def test_rotated_block_keeps_its_attributes_on_the_block(self):
        # Rotated 90 degrees about the insertion point (100, 100), as the editor shows it.
        msp, _ = self.save([self.insert_op((100, 100), R90, (1, 1, 1), [
            self.placement("NO", (115, 85), R90, 5), self.placement("NAME", (88, 88), R90, 4)])])
        insert, attribs = self.placed(msp)
        self.assertAlmostEqual(insert.dxf.rotation, 90)
        self.assertEqual(_r(attribs["NO"].dxf.insert), (115, 85))
        # The centre of the text turned with it: (100, 85) -> (115, 100).
        self.assertEqual(_r(attribs["NO"].dxf.align_point), (115, 100))
        self.assertAlmostEqual(attribs["NO"].dxf.rotation, 90)
        self.assertEqual(_r(attribs["NAME"].dxf.insert), (88, 88))
        self.assertAlmostEqual(attribs["NAME"].dxf.rotation, 90)
        self.assertEqual([attribs[t].dxf.text for t in ("NO", "NAME")], ["A-101", "LOBBY"])

    def test_scaled_and_mirrored_blocks(self):
        msp, _ = self.save([self.insert_op((100, 100), 0, (2, 2, 1), [
            self.placement("NO", (70, 70), 0, 10), self.placement("NAME", (76, 124), 0, 8)])])
        _, attribs = self.placed(msp)
        self.assertEqual(_r(attribs["NO"].dxf.insert), (70, 70))
        self.assertEqual(_r(attribs["NO"].dxf.align_point), (100, 70))
        self.assertAlmostEqual(attribs["NO"].dxf.height, 10)
        # Mirrored about x = 0; the editor keeps attribute text readable.
        msp, _ = self.save([self.insert_op((-100, 100), math.pi, (1, -1, 1), [
            self.placement("NO", (-115, 85), 0, 5), self.placement("NAME", (-112, 112), 0, 4)])])
        insert, attribs = self.placed(msp)
        self.assertAlmostEqual(insert.dxf.yscale, -1)
        self.assertEqual(_r(attribs["NO"].dxf.align_point), (-100, 85))
        self.assertAlmostEqual(attribs["NO"].dxf.rotation, 0)

    def test_rotated_copy_places_the_copied_attributes(self):
        attributes = [self.placement("NO", (315, 85), R90, 5, handle="", sourceHandle=self.attribs["NO"].dxf.handle),
                      self.placement("NAME", (288, 88), R90, 4, handle="", sourceHandle=self.attribs["NAME"].dxf.handle)]
        op = {"type": "add_insert", "entity": "INSERT", "copyOf": self.insert.dxf.handle, "blockName": "TAGBLK", "layer": "0",
              "insert": [300, 100, 0], "rotation": R90, "scale": [1, 1, 1], "attributes": attributes, "clientShapeId": "copy-1"}
        msp, report = self.save([op])
        copy = next(i for i in msp.query("INSERT") if i.dxf.handle != self.insert.dxf.handle)
        attribs = {a.dxf.tag: a for a in copy.attribs}
        self.assertEqual(_r(attribs["NO"].dxf.align_point), (315, 100))
        self.assertEqual(_r(attribs["NAME"].dxf.insert), (288, 88))
        self.assertEqual([attribs[t].dxf.text for t in ("NO", "NAME")], ["A-101", "LOBBY"])
        _, original = self.placed(msp)
        self.assertEqual(_r(original["NO"].dxf.align_point), (100, 85))
        # A copy of a saved copy: its attributes are found by tag.
        tagged = [dict(a, sourceHandle="") for a in attributes]
        msp, _ = self.save([dict(op, attributes=tagged)])
        copy = next(i for i in msp.query("INSERT") if i.dxf.handle != self.insert.dxf.handle)
        self.assertEqual(_r({a.dxf.tag: a for a in copy.attribs}["NO"].dxf.align_point), (315, 100))

    def test_rotation_without_every_attribute_placed_is_refused(self):
        message = self.refused([self.insert_op((100, 100), R90, (1, 1, 1), [self.placement("NO", (115, 85), R90, 5)])])
        self.assertIn("블록 속성", message)
        # Without placements the writer still refuses instead of leaving the text behind.
        op = self.insert_op((100, 100), R90, (1, 1, 1), [])
        del op["attributes"]
        self.assertIn("저장할 수 없어", self.refused([op]))

    def test_rotated_justified_text_and_mtext(self):
        # The editor rotates a TEXT about its centre: the insertion point moves,
        # the alignment point (where AutoCAD anchors centred text) stays.
        msp, _ = self.save([
            {"type": "update", "handle": self.text.dxf.handle, "sourceHandle": self.text.dxf.handle, "entity": "TEXT",
             "insert": [150, 45, 0], "rotation": R90, "height": 10},
            {"type": "update", "handle": self.mtext.dxf.handle, "sourceHandle": self.mtext.dxf.handle, "entity": "MTEXT",
             "insert": [0, 0, 0], "rotation": R90, "height": 5}])
        text = msp.query("TEXT")[0]
        self.assertEqual(_r(text.dxf.insert), (150, 45))
        self.assertEqual(_r(text.dxf.align_point), (150, 60))
        self.assertAlmostEqual(text.dxf.rotation, 90)
        mtext = msp.query("MTEXT")[0]
        self.assertAlmostEqual(mtext.get_rotation(), 90, places=4)

    @skipUnless(find_oda(), "ODA File Converter is only used for local review")
    def test_another_reader_opens_turned_blocks_with_attributes(self):
        import ezdxf

        copy = {"type": "add_insert", "entity": "INSERT", "copyOf": self.insert.dxf.handle, "blockName": "TAGBLK", "layer": "0",
                "insert": [300, 100, 0], "rotation": R90, "scale": [1, -1, 1], "clientShapeId": "copy-1", "attributes": [
                    self.placement("NO", (315, 85), 0, 5, handle="", sourceHandle=self.attribs["NO"].dxf.handle),
                    self.placement("NAME", (288, 88), 0, 4, handle="", sourceHandle=self.attribs["NAME"].dxf.handle)]}
        self.save([self.insert_op((100, 100), R90, (2, 2, 1), [self.placement("NO", (130, 70), R90, 10), self.placement("NAME", (76, 76), R90, 8)]), copy])
        source, target = self.tmp / "oda-in", self.tmp / "oda-out"
        source.mkdir()
        target.mkdir()
        shutil.copy(self.tmp / "out.dwg", source / "out.dwg")
        subprocess.run([find_oda(), str(source), str(target), "ACAD2018", "DXF", "0", "1"], capture_output=True, timeout=300)
        self.assertFalse(list(target.glob("*.err")), [p.read_text(errors="replace")[:300] for p in target.glob("*.err")])
        inserts = ezdxf.readfile(target / "out.dxf").modelspace().query("INSERT")
        self.assertEqual(len(inserts), 2)
        self.assertEqual(sorted(a.dxf.text for i in inserts for a in i.attribs), ["A-101", "A-101", "LOBBY", "LOBBY"])
