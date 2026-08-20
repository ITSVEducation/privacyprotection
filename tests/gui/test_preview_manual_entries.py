"""gui/preview_dialog.py の collect_manual_entries() 直接ユニットテスト。

GUI層は原則として手動確認だが、この関数は QApplication を必要としない純粋
関数であり、「マスク実行時に手動追加検出をカスタム辞書へ自動登録する」仕様の
対象選別（有効な manual のみ）を一手に担うためユニットテストする。
"""
from privacyprotection.core.models import Detection
from privacyprotection.gui.preview_dialog import collect_manual_entries


def _det(text, category="CUSTOM", source="manual", enabled=True, start=0):
    return Detection(text=text, category=category, start=start,
                     end=start + len(text), source=source, enabled=enabled)


def test_collects_enabled_manual_detections():
    dets = [[_det("プロジェクトX")], [_det("山田商事", category="ORG", start=5)]]
    assert collect_manual_entries(dets) == {
        "プロジェクトX": "CUSTOM", "山田商事": "ORG"}


def test_excludes_disabled_manual_detections():
    dets = [[_det("プロジェクトX", enabled=False)]]
    assert collect_manual_entries(dets) == {}


def test_excludes_non_manual_sources():
    dets = [[_det("山田太郎", category="PERSON", source="ner"),
             _det("03-1234-5678", category="PHONE", source="pattern", start=10),
             _det("既存語", source="dictionary", start=30)]]
    assert collect_manual_entries(dets) == {}


def test_first_occurrence_wins_for_same_text():
    # 「同じ語をまとめて扱う」ONで全出現追加した後、片方だけカテゴリ変更
    # された場合など。最初に現れたものを採用する。
    dets = [[_det("プロジェクトX"), _det("プロジェクトX", category="ORG", start=20)]]
    assert collect_manual_entries(dets) == {"プロジェクトX": "CUSTOM"}


def test_category_change_is_reflected():
    dets = [[_det("山田太郎", category="PERSON")]]
    assert collect_manual_entries(dets) == {"山田太郎": "PERSON"}


def test_empty_detections():
    assert collect_manual_entries([]) == {}
    assert collect_manual_entries([[], []]) == {}
