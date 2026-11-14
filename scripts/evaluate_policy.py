"""Offline policy regression metrics against existing repository labels, not market accuracy."""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'src'))
from job_radar.eligibility import assess_application  # noqa: E402

cases = json.loads((root / 'tests/fixtures/application_priority.json').read_text())
counts = dict(tp=0, tn=0, fp=0, fn=0)
for case in cases:
    expected = case['expected'] != 'EXCLUDED'
    actual = assess_application({**case, 'screening_status': 'eligible'})['application_priority'] != 'EXCLUDED'
    counts['tp' if expected and actual else 'fn' if expected else 'fp' if actual else 'tn'] += 1
print(json.dumps({'dataset': 'existing synthetic application_priority fixtures', 'count': len(cases),
                  **counts, 'precision': counts['tp'] / (counts['tp'] + counts['fp']) if counts['tp'] + counts['fp'] else None,
                  'recall': counts['tp'] / (counts['tp'] + counts['fn']) if counts['tp'] + counts['fn'] else None}, indent=2))
