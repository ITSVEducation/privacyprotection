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

def test_write_mapping_falls_back_to_raw_category_when_out_of_vocabulary(tmp_path):
    """最終レビュー Finding 7: 手編集のconfig.jsonのカスタム辞書が固定10種
    以外のカテゴリ(例:"WEIRD")を持つエントリでもwrite_mappingがKeyErrorで
    落ちないこと。read_mapping側は既存仕様どおり未知ラベルを"CUSTOM"に
    落とすため、安全に(情報を失いつつも)往復できることを確認する。"""
    table = MappingTable(entries=[
        MappingEntry("【WEIRD_1】", "謎の値", "WEIRD", 1),
    ])
    p = tmp_path / "weird.pmap.csv"
    write_mapping(table, p)
    assert "WEIRD" in p.read_text(encoding="utf-8-sig")
    loaded = read_mapping(p)
    assert loaded.entries[0].category == "CUSTOM"
    assert loaded.entries[0].original == "謎の値"


def test_read_rejects_duplicate_token(tmp_path):
    # 2つの独立した対応表をマージした場合などに、同一トークンが異なる元の値を
    # 指す状態になり得る（Restorer が誤ったPIIを復元する重大なリスク）。
    p = tmp_path / "dup.pmap.csv"
    p.write_text(
        "トークン,元の値,種別,出現回数\r\n"
        "【人名_1】,山田太郎,人名,1\r\n"
        "【人名_1】,鈴木一郎,人名,1\r\n",
        encoding="utf-8-sig",
    )
    with pytest.raises(ValueError):
        read_mapping(p)
