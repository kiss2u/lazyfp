from typing import Optional
from extractors.base import BaseExtractor


class AmountExtractor(BaseExtractor):
    def clean(self, value: str) -> str:
        return value.replace(",", "").replace("¥", "").replace("￥", "").strip()

    def validate(self, value: str) -> bool:
        try:
            val = float(value)
            if val < 0 or val > 100000000:
                return False
            return True
        except (ValueError, TypeError):
            return False

    def fallback(self, text: str, filename: str) -> Optional[str]:
        return None
