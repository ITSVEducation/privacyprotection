import pytest
from privacyprotection.core.mapping_io import read_mapping, write_mapping
from privacyprotection.core.models import MappingEntry, MappingTable


def test_roundtrip(tmp_path):
    table = MappingTable(entries=[
        MappingEntry("【人名_1】", "山田太郎", "PERSON", 5),
        MappingEntry("【組織_1】", 'カンマ,と"引用符"入り', "ORG", 1),
        MappingEntry("【カスタム_1】", "改行\n入り", "CUSTOM", 2),
    ])
    p = tmp_path / "out.pmap.csv"
    write_mapping(table, p)
    loaded = read_mapping(p)
    assert [(e.token, e.original, e.count) for e in loaded.entries] == \
           [(e.token, e.original, e.count) for e in table.entries]

def test_file_has_bom_and_japanese_header(tmp_path):
    p = tmp_path / "out.pmap.csv"
    write_mapping(MappingTable(), p)
    raw = p.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8-sig").splitlines()[0] == "トークン,元の値,種別,出現回数"

def test_read_rejects_wrong_header(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("a,b,c,d\r\nx,y,z,1\r\n", encoding="utf-8-sig")
    with pytest.raises(ValueError):
        read_mapping(p)
