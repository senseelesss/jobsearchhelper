import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


def load_candidate_profile(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Candidate profile must be a YAML object")
    return value


def candidate_profile_hash(profile: dict[str, Any]) -> str:
    canonical = json.dumps(
        profile,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
