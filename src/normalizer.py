# normalizer.py
# normalizer.py
"""
Parse UiAutomator XML into a compact, consistent structure with element bounds,
text, ids, and a stable element_id hash. This decouples planners from raw XML.
"""
import hashlib
import re
from typing import Dict, List, Any
from lxml import etree

BOUND_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

def parse_bounds(bounds_str: str):
    m = BOUND_RE.match(bounds_str)
    if not m:
        return None
    left, top, right, bottom = map(int, m.groups())
    return [left, top, right, bottom]

def sha1_of(s: str):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def normalize(xml_path: str) -> Dict[str, Any]:
    parser = etree.XMLParser(recover=True, ns_clean=True)
    tree = etree.parse(xml_path, parser)
    root = tree.getroot()
    elements = []
    # uiautomator dump uses nodes under root with tag 'node'
    for node in root.iter("node"):
        attrib = node.attrib
        resource_id = attrib.get("resource-id") or ""
        text = attrib.get("text") or ""
        content_desc = attrib.get("content-desc") or ""
        clazz = attrib.get("class") or ""
        bounds_s = attrib.get("bounds") or ""
        clickable = attrib.get("clickable") == "true"
        focusable = attrib.get("focusable") == "true"
        bounds = parse_bounds(bounds_s) or [0,0,0,0]
        left, top, right, bottom = bounds
        center = [ (left+right)//2, (top+bottom)//2 ]
        uniq = f"{resource_id}|{text}|{bounds_s}"
        element_id = sha1_of(uniq)
        elements.append({
            "element_id": element_id,
            "resource_id": resource_id,
            "text": text,
            "content_desc": content_desc,
            "class": clazz,
            "bounds": bounds,
            "clickable": clickable,
            "focusable": focusable,
            "center": center
        })
    # Also include a mapping from element_id -> element for quick lookup
    by_id = {e["element_id"]: e for e in elements}
    return {"elements": elements, "by_id": by_id}
