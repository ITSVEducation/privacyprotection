from pathlib import Path

from privacyprotection.services.intent import (
    FOLDER_MAPPING_NAME, Intent, decide_file_intent, decide_folder_intent,
    decide_text_intent, needs_confirmation,
)


# --- テキスト ---------------------------------------------------------

def test_text_without_token_is_mask():
    assert decide_text_intent("山田太郎です") is Intent.MASK


def test_text_with_token_is_restore():
    assert decide_text_intent("【人名_1】様、お世話になります") is Intent.RESTORE


def test_text_action_mode_overrides_auto():
    assert decide_text_intent("【人名_1】", action_mode="mask") is Intent.MASK
    assert decide_text_intent("平文", action_mode="restore") is Intent.RESTORE


# --- ファイル ---------------------------------------------------------

def test_file_with_own_sidecar_mapping_is_restore(tmp_path):
    f = tmp_path / "memo_masked.txt"
    f.write_text("x", encoding="utf-8")
    (tmp_path / "memo_masked.txt.pmap.csv").write_text("", encoding="utf-8")
    assert decide_file_intent(f, ["x"]) is Intent.RESTORE


def test_masked_named_file_with_folder_mapping_is_restore(tmp_path):
    f = tmp_path / "memo_masked.txt"
    f.write_text("x", encoding="utf-8")
    (tmp_path / FOLDER_MAPPING_NAME).write_text("", encoding="utf-8")
    assert decide_file_intent(f, ["x"]) is Intent.RESTORE


def test_plain_file_next_to_folder_mapping_is_mask(tmp_path):
    # フォルダ一括処理は既定で元ファイルと同じフォルダに出力するため、
    # 元ファイルの隣にも _folder.pmap.csv が存在する。マスク済みの命名
    # （*_masked）でないファイルまで復元と誤判定してはいけない。
    f = tmp_path / "memo.txt"
    f.write_text("平文", encoding="utf-8")
    (tmp_path / FOLDER_MAPPING_NAME).write_text("", encoding="utf-8")
    assert decide_file_intent(f, ["平文"]) is Intent.MASK


def test_file_with_token_in_fragments_is_restore(tmp_path):
    f = tmp_path / "downloaded.txt"
    f.write_text("x", encoding="utf-8")
    assert decide_file_intent(f, ["こんにちは", "【電話_2】まで"]) is Intent.RESTORE


def test_plain_file_is_mask(tmp_path):
    f = tmp_path / "memo.txt"
    f.write_text("平文", encoding="utf-8")
    assert decide_file_intent(f, ["平文"]) is Intent.MASK


def test_file_action_mode_overrides_auto(tmp_path):
    f = tmp_path / "memo_masked.txt"
    f.write_text("x", encoding="utf-8")
    (tmp_path / "memo_masked.txt.pmap.csv").write_text("", encoding="utf-8")
    assert decide_file_intent(f, ["【人名_1】"], action_mode="mask") is Intent.MASK
    assert decide_file_intent(tmp_path / "plain.txt", ["平文"],
                              action_mode="restore") is Intent.RESTORE


# --- フォルダ ---------------------------------------------------------

def test_folder_with_mapping_is_restore(tmp_path):
    (tmp_path / FOLDER_MAPPING_NAME).write_text("", encoding="utf-8")
    assert decide_folder_intent(tmp_path) is Intent.RESTORE


def test_folder_without_mapping_is_mask(tmp_path):
    assert decide_folder_intent(tmp_path) is Intent.MASK


def test_folder_action_mode_overrides_auto(tmp_path):
    (tmp_path / FOLDER_MAPPING_NAME).write_text("", encoding="utf-8")
    assert decide_folder_intent(tmp_path, action_mode="mask") is Intent.MASK
    assert decide_folder_intent(tmp_path, action_mode="restore") is Intent.RESTORE


# --- 確認ダイアログの要否 -------------------------------------------------
# 「復元と判定したが、動作が明示的に固定されていなければ確認する」という
# 決定ロジックそのもの（GUI側の _wants_restore はこの結果に従ってダイアログを
# 出すだけ）。GUIにテストを持たない方針のため、このロジックはここに置く。

def test_restore_auto_needs_confirmation():
    assert needs_confirmation(Intent.RESTORE, "auto") is True


def test_restore_forced_restore_skips_confirmation():
    assert needs_confirmation(Intent.RESTORE, "restore") is False


def test_restore_forced_mask_skips_confirmation():
    assert needs_confirmation(Intent.RESTORE, "mask") is False


def test_mask_auto_never_needs_confirmation():
    assert needs_confirmation(Intent.MASK, "auto") is False


def test_mask_forced_restore_never_needs_confirmation():
    assert needs_confirmation(Intent.MASK, "restore") is False
