import shutil
import sys
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from prepare_sticker import normalize


class StickerPrepareTests(unittest.TestCase):
    def test_normalizer_adds_transparent_padding_and_reuses_cache(self):
        work = ROOT / "test" / ".tmp_sticker_prepare"
        shutil.rmtree(work, ignore_errors=True)
        source = work / "custom" / "开心_夸奖.png"
        cache = work / "cache"
        source.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (400, 200), (255, 0, 0, 255)).save(source)
        try:
            first = normalize(source, cache, 0.62, 512)
            self.assertAlmostEqual(first["content_width"] / first["width"], 0.82, places=2)
            self.assertAlmostEqual(first["content_height"] / first["height"], 0.82, places=2)
            self.assertLessEqual(first["content_width"], 318)
            self.assertLess(first["content_height"], first["content_width"])
            self.assertEqual(first["layout"], "proportional_padding")
            self.assertTrue(Path(first["path"]).exists())
            second = normalize(source, cache, 0.62, 512)
            self.assertTrue(second["cached"])
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_normalizer_removes_only_edge_connected_white_background(self):
        work = ROOT / "test" / ".tmp_sticker_white_trim"
        shutil.rmtree(work, ignore_errors=True)
        source = work / "custom" / "害羞.png"
        cache = work / "cache"
        source.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (400, 400), (255, 255, 255))
        for x in range(140, 260):
            for y in range(140, 260):
                image.putpixel((x, y), (160, 80, 80))
        image.save(source)
        try:
            result = normalize(source, cache, 0.62, 512)
            self.assertTrue(result["white_background_removed"])
            self.assertEqual(result["width"], 317)
            self.assertEqual(result["height"], 317)
            self.assertEqual(result["content_width"], 260)
            self.assertEqual(result["content_height"], 260)
            with Image.open(result["path"]).convert("RGBA") as prepared:
                self.assertEqual(prepared.getpixel((0, 0))[3], 0)
                self.assertGreater(prepared.getpixel((256, 256))[3], 0)
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
