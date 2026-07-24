"""テキスト系ファイル。元エンコーディング・改行をバイト忠実に維持する（設計書 4.7）。"""
from __future__ import annotations

from pathlib import Path

from ..core.models import Fragment
from .base import FileHandler


class TextHandler(FileHandler):
    extensions = [".txt", ".csv", ".json", ".md", ".log", ".xml", ".html", ".yaml", ".yml"]

    def detect_encoding(self, path: Path) -> str:
        raw = path.read_bytes()
        # cp932 は 0x00-0x7F・0xA0-0xDF・0xFD-0xFF 等ほぼ全ての単独バイトを
        # 何らかの文字として解釈できてしまうため、decode() の成功可否だけでは
        # バイナリデータ（例: UTF-16 等の非対応エンコーディング）を判定不能として
        # 検出できない。テキストファイルに NUL バイトが含まれることは通常ない
        # ため、これを含む場合は判定不能として扱う。
        if b"\x00" in raw:
            raise UnicodeError(f"エンコーディングを判定できません: {path.name}")
        for enc in ("utf-8-sig", "utf-8", "cp932"):
            try:
                raw.decode(enc)
                # BOMなしファイルを utf-8-sig で読むと BOM 誤除去はないが、
                # BOM付きは utf-8-sig が先にマッチするためこの順で安全
                if enc == "utf-8-sig" and not raw.startswith(b"\xef\xbb\xbf"):
                    continue
                return enc
            except UnicodeDecodeError:
                continue
        from charset_normalizer import from_bytes
        best = from_bytes(raw).best()
        if best is None:
            raise UnicodeError(f"エンコーディングを判定できません: {path.name}")
        return best.encoding

    def read_fragments(self, path: Path) -> list[Fragment]:
        enc = self.detect_encoding(path)
        # newline を変換しないよう bytes からデコード（改行コード維持）
        text = path.read_bytes().decode(enc)
        return [Fragment(text=text, location="text", encoding=enc)]

    def write_fragments(self, src: Path, dst: Path, masked: list[Fragment]) -> None:
        enc = masked[0].encoding or self.detect_encoding(src)
        dst.write_bytes(masked[0].text.encode(enc))
