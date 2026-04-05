import re
from abc import ABC, abstractmethod
from typing import Optional


class BaseExtractor(ABC):
    def __init__(self, rules: list[dict]):
        self._compiled = []
        for r in rules:
            self._compiled.append({
                "pattern": re.compile(r["pattern"], r.get("flags", 0)),
                "desc": r.get("desc", ""),
                "group": r.get("group", 1),
                "source": r.get("source", "text"),
            })

    def extract(self, text: str, filename: str = "") -> Optional[str]:
        for rule in self._compiled:
            if rule["source"] == "filename":
                result = self._match_filename(rule["pattern"], filename)
            else:
                result = self._match_text(rule["pattern"], rule["group"], text)
            if result:
                cleaned = self.clean(result)
                if cleaned and self.validate(cleaned):
                    return cleaned
        return self.fallback(text, filename)

    def _match_text(self, pattern: re.Pattern, group: int, text: str) -> Optional[str]:
        m = pattern.search(text)
        if m:
            try:
                return m.group(group)
            except IndexError:
                return None
        return None

    def _match_filename(self, pattern: re.Pattern, filename: str) -> Optional[str]:
        if not filename:
            return None
        m = pattern.search(filename)
        if m:
            try:
                return m.group(1)
            except IndexError:
                return None
        return None

    def clean(self, value: str) -> str:
        return value.strip()

    def validate(self, value: str) -> bool:
        return bool(value)

    def fallback(self, text: str, filename: str) -> Optional[str]:
        return None
