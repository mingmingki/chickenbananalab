import json
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

FIXTURES = Path(settings.BASE_DIR) / "core" / "test_fixtures" / "cad"
# A LINE, an MTEXT multileader ("기초 F1") and a block multileader (block
# "기호"): ezdxf R2018 DXF converted to DWG AC1032 by ODA.
MLEADER = FIXTURES / "mleader_ac1032.dwg"


@skipUnless(EXECUTABLE is not None, "ACadSharp runtime is required")
class CadDwgMultiLeaderTests(SimpleTestCase):
    """Drawings with multileaders (MLEADER) save.

    ACadSharp wrote the AC1018 MLEADER without the R2007- arrowhead count and,
    for block content, without the has-contents-block bit; the reread lost the
    objects and every save of such a drawing was refused.  ODA could not open
    the written file at all.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cbl-mleader-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def save(self, source=MLEADER, ops=()):
        ops_path = self.tmp / "ops.json"
        ops_path.write_text(json.dumps({"ops": list(ops)}), encoding="utf-8")
        output = self.tmp / "saved.dwg"
        return output, _run_writer([source, output, "AC1018", ops_path])

    def test_multileaders_are_written_and_read_back(self):
        output, report = self.save()
        self.assertEqual(report["source"]["Counts"].get("MultiLeader"), 2)
        self.assertEqual(report["reread"]["Counts"].get("MultiLeader"), 2)
        self.assertFalse([n for n in report["rereadNotifications"] if n.get("type") == "Error"])
        core_views._cbl_free_dwg_save_local_validate_v1(MLEADER, output, None, [], report)

    def test_an_edit_next_to_them_saves(self):
        meta = core_views._cbl_free_dwg_acadsharp_metadata_v1(MLEADER)
        line = next(e for e in meta["entities"] if e.get("space") == "modelspace" and e["type"] == "LINE")
        ops = [{"type": "move", "handle": line["handle"], "delta": [0, -100, 0]}]
        output, report = self.save(ops=ops)
        core_views._cbl_free_dwg_save_local_validate_v1(MLEADER, output, None, ops, report)

    def test_a_saved_drawing_saves_again(self):
        first, _ = self.save()
        again = self.tmp / "first.dwg"
        shutil.copy(first, again)
        output, report = self.save(source=again)
        self.assertEqual(report["reread"]["Counts"].get("MultiLeader"), 2)
        core_views._cbl_free_dwg_save_local_validate_v1(again, output, None, [], report)

    def test_dxf_multileaders_reach_the_dwg(self):
        import ezdxf
        from ezdxf.math import Vec2
        from ezdxf.render.mleader import ConnectionSide

        doc = ezdxf.new("R2018", setup=True)
        leader = doc.modelspace().add_multileader_mtext("Standard")
        leader.set_content("지시선")
        leader.add_leader_line(ConnectionSide.left, [Vec2(0, 0)])
        leader.build(insert=Vec2(500, 500))
        dxf = self.tmp / "leader.dxf"
        doc.saveas(dxf)
        run = subprocess.run([str(EXECUTABLE), "--dwg-from-dxf", str(dxf), str(self.tmp / "leader.dwg")],
                             capture_output=True, timeout=300)
        self.assertEqual(run.returncode, 0, run.stderr[-800:])
        report = json.loads(run.stdout, strict=False)
        self.assertEqual(report["dropped"], {})
        self.assertEqual(report["reread"]["Counts"].get("MultiLeader"), 1)

    @skipUnless(find_oda(), "ODA File Converter is only used for local review")
    def test_another_reader_opens_the_saved_drawing(self):
        import ezdxf

        output, _ = self.save()
        source, target = self.tmp / "oda-in", self.tmp / "oda-out"
        source.mkdir()
        target.mkdir()
        shutil.copy(output, source / "saved.dwg")
        subprocess.run([find_oda(), str(source), str(target), "ACAD2018", "DXF", "0", "1"], capture_output=True, timeout=300)
        self.assertFalse(list(target.glob("*.err")), [p.read_text(errors="replace")[:300] for p in target.glob("*.err")])
        leaders = list(ezdxf.readfile(target / "saved.dxf").modelspace().query("MULTILEADER"))
        self.assertEqual(len(leaders), 2)
        texts = [leader.context.mtext.default_content for leader in leaders if leader.context.mtext]
        self.assertEqual(texts, ["기초 F1"])
