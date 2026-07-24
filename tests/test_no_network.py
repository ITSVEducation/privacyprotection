"""設計書 9.1: 自社コードにネットワークAPI呼び出しが含まれないことの静的検査。"""
import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"

FORBIDDEN = re.compile(
    r"^\s*(?:import|from)\s+(socket|urllib|requests|http\.client|httpx|aiohttp|ftplib|smtplib|telnetlib)\b",
    re.MULTILINE,
)


def test_no_network_imports_in_source():
    violations = []
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for m in FORBIDDEN.finditer(text):
            violations.append(f"{py.relative_to(SRC)}: {m.group().strip()}")
    assert violations == [], f"ネットワークAPIのimportを検出: {violations}"


def test_detection_of_forbidden_import_works():
    # 検査自体が機能していることを確認（自己テスト）
    assert FORBIDDEN.search("import socket")
    assert FORBIDDEN.search("from urllib import request")
    assert not FORBIDDEN.search("import json")
