"""EPSS percentile display regression (Node.js is optional for Python users)."""
from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class EpssDisplayTest(unittest.TestCase):
    def test_percentile_means_top_fraction_not_percentile_itself(self):
        html = Path(__file__).resolve().parents[1] / "ai_yori_murasaki" / "web" / "index.html"
        script = r"""
const fs = require('fs');
const assert = require('assert');
const html = fs.readFileSync(process.argv[1], 'utf8');
const start = html.indexOf('function enrichmentEpssText(rec)');
const end = html.indexOf('function renderEnrichmentBlock(', start);
assert(start >= 0 && end > start, 'EPSS renderer not found');
const fn = new Function('ENRICH_EPSS_TEXT',
    html.slice(start, end) + '\nreturn enrichmentEpssText;')({});
assert(fn({status:'scored',epss:0.97,percentile:0.99}).includes('上位1％'));
assert(fn({status:'scored',epss:0.01,percentile:0.01}).includes('上位99％'));
assert(fn({status:'scored',epss:0.97}).includes('順位未記録'));
assert(fn({status:'scored',epss:0.97,percentile:1.5}).includes('順位未記録'));
"""
        cp = subprocess.run(
            ["node", "-e", script, str(html)],
            capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(cp.returncode, 0, cp.stderr)


if __name__ == "__main__":
    unittest.main()
