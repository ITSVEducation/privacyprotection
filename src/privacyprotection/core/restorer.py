"""復元エンジン（設計書 5.2）。完全一致のみ、曖昧マッチはしない。"""
from __future__ import annotations

import re

from .models import TOKEN_RE, MappingTable, RestoreResult


class Restorer:
    def __init__(self, mapping: MappingTable):
        self._map = {e.token: e.original for e in mapping.entries}

    def restore(self, text: str) -> RestoreResult:
        unknown: list[str] = []

        def _sub(m: re.Match) -> str:
            token = m.group()
            if token in self._map:
                return self._map[token]
            if token not in unknown:
                unknown.append(token)
            return token

        return RestoreResult(text=TOKEN_RE.sub(_sub, text), unknown_tokens=unknown)
