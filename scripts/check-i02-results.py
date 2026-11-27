"""Fail closed on missing, skipped, failed or unexpected mandatory I02 tests."""
import sys
import xml.etree.ElementTree as ET
from collections import Counter

EXPECTED = {
    "tests.test_i02_request_items": {
        "test_valid_and_historical_items": 1,
        "test_invalid_item": 3,
        "test_duplicate_reservation": 1,
        "test_actor_time_pair": 2,
    },
    "tests.test_i02_shipment_receipt_links": {
        "test_receipt_metadata_pair": 2,
        "test_nullable_legacy_links": 1,
        "test_invalid_fulfillment_reference": 2,
        "test_fulfillment_unique_constraint": 2,
        "test_duplicate_request_link": 1,
        "test_duplicate_request_item_link": 1,
    },
    "tests.test_i02_writeoff_requests": {
        "test_writeoff_valid_reason": 3,
        "test_writeoff_invalid_input": 4,
        "test_writeoff_actor_time_pair": 4,
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
    print(f"Mandatory I02 manifest verified: {len(cases)} passing cases")

if __name__ == "__main__":
    main(sys.argv[1])
