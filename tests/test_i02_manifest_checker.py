"""Regression tests for the mandatory I02 report gate."""
import runpy
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

CHECKER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/check-i02-results.py"))

def report():
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for cls, names in CHECKER["EXPECTED"].items():
        for name, count in names.items():
            for i in range(count):
                ET.SubElement(suite, "testcase", classname=cls,
                              name=name if count == 1 else f"{name}[{i}]")
    return root, suite

@pytest.mark.parametrize("mutation", ["missing", "skipped", "failure", "error", "duplicate", "unexpected", "empty"])
def test_manifest_rejects_invalid_report(tmp_path, mutation):
    root, suite = report()
    if mutation == "missing":
        suite.remove(suite[0])
    elif mutation in ("skipped", "failure", "error"):
        ET.SubElement(suite[0], mutation)
    elif mutation == "duplicate":
        ET.SubElement(suite, "testcase", **suite[0].attrib)
    elif mutation == "unexpected":
        suite[0].set("name", "unexpected")
    else:
        suite.clear()
    path = tmp_path / "report.xml"
    ET.ElementTree(root).write(path)
    with pytest.raises(SystemExit):
        CHECKER["main"](path)

def test_manifest_accepts_complete_report(tmp_path):
    root, _ = report()
    path = tmp_path / "report.xml"
    ET.ElementTree(root).write(path)
    CHECKER["main"](path)


def test_manifest_rejects_malformed_xml(tmp_path):
    path = tmp_path / "report.xml"
    path.write_text("<testsuites>")
    with pytest.raises(ET.ParseError):
        CHECKER["main"](path)
