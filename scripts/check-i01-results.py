"""Fail closed on missing, skipped, failed or unexpected mandatory I01 tests."""
import sys
import xml.etree.ElementTree as ET
from collections import Counter

EXPECTED = {
    "tests.test_i01_pg_fixture_guard": {
        "test_guard_rejects_unsafe_configuration": 6,
        "test_guard_accepts_explicit_isolated_configuration": 1,
    },
    "tests.test_i01_pg_runtime_identity": {
        "test_runtime_identity": 1,
        "test_other_identity_rejected": 3,
        "test_wrong_database_rejected": 1,
        "test_rollback_fixture_writes_synthetic_probe": 1,
        "test_rollback_fixture_leaves_no_rows": 1,
        "test_fixture_reads_migrated_application_schema": 1,
        "test_independent_sessions_and_uncommitted_visibility": 1,
        "test_conflicting_insert_and_committed_visibility": 2,
        "test_synthetic_card_relationships": 1,
        "test_synthetic_reference_entities": 1,
        "test_synthetic_manifest_and_request": 1,
        "test_cleanup_rejects_unsafe_arguments": 2,
        "test_cleanup_rejects_wrong_identity": 1,
        "test_cleanup_preserves_unrelated_probe": 1,
    },
}

def main(path):
    cases = ET.parse(path).getroot().findall(".//testcase")
    actual = Counter()
    seen = set()
    for case in cases:
        cls, name = case.attrib["classname"], case.attrib["name"]
        if (cls, name) in seen or any(case.find(tag) is not None for tag in ("skipped", "failure", "error")):
            raise SystemExit(f"Non-passing or duplicate mandatory case: {cls}.{name}")
        seen.add((cls, name))
        actual[cls, name.split("[", 1)[0]] += 1
    expected = Counter({(cls, name): count for cls, names in EXPECTED.items() for name, count in names.items()})
    if actual != expected:
        raise SystemExit(f"Mandatory manifest mismatch: missing={expected-actual}; unexpected={actual-expected}")
    print(f"Mandatory I01 manifest verified: {len(cases)} passing cases")

if __name__ == "__main__":
    main(sys.argv[1])
