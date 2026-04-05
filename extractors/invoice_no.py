import re
from typing import Optional
from extractors.base import BaseExtractor


class InvoiceNoExtractor(BaseExtractor):
    def clean(self, value: str) -> str:
        return re.sub(r"[,\s]", "", value).strip()

    def validate(self, value: str) -> bool:
        if not value:
            return False
        if not value.isdigit():
            return False
        if len(value) < 8:
            return False
        return True

    def fallback(self, text: str, filename: str) -> Optional[str]:
        nums_8 = re.findall(r"\b(\d{8})\b", text)
        for n in nums_8:
            if n.startswith("202"):
                continue
            return n
        return None
