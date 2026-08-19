from privacyprotection.config import (
    AppConfig, export_dictionary_csv, import_dictionary_csv,
    load_config, save_config,
)

def test_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "none.json")
    assert cfg.mask_mode == "token"
    assert cfg.skip_preview is False
    assert cfg.custom_dictionary == {}
    assert "PERSON" in cfg.enabled_categories

def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    cfg = AppConfig(enabled_categories={"PERSON"},
                    custom_dictionary={"PRJ-001": "CUSTOM"},
                    output_dir="C:/out", skip_preview=True, mask_mode="redact")
    save_config(cfg, p)
    loaded = load_config(p)
    assert loaded == cfg

def test_dictionary_csv_roundtrip_and_duplicate_warning(tmp_path):
    p = tmp_path / "dict.csv"
    p.write_text("語句,種別\r\n株式会社サンプル,カスタム\r\n株式会社サンプル,組織\r\n",
                 encoding="utf-8-sig")
    d, warnings = import_dictionary_csv(p)
    assert d == {"株式会社サンプル": "CUSTOM"}  # 先勝ち
    assert len(warnings) == 1
    out = tmp_path / "out.csv"
    export_dictionary_csv(d, out)
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")

def test_new_ui_fields_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    cfg = AppConfig(action_mode="restore", advanced_expanded=True)
    save_config(cfg, p)
    loaded = load_config(p)
    assert loaded.action_mode == "restore"
    assert loaded.advanced_expanded is True


def test_new_ui_fields_default_when_missing(tmp_path):
    p = tmp_path / "config.json"
    p.write_text('{"mask_mode": "token"}', encoding="utf-8")
    loaded = load_config(p)
    assert loaded.action_mode == "auto"
    assert loaded.advanced_expanded is False
