import re
from typing import Optional
from extractors.base import BaseExtractor


_INVALID_NAME_TOKENS = ["名称", "购买方", "销售方", "名", "称", "：", ":", "购", "买", "售", "方", "日", "省", "税务局", "税务"]


class CompanyNameExtractor(BaseExtractor):
    def __init__(self, rules: list[dict], field_name: str = "purchaser"):
        super().__init__(rules)
        self._field_name = field_name

    def clean(self, value: str) -> Optional[str]:
        if not value:
            return None
        n = re.sub(r"[\s\u3000\xa0]+", "", value)
        for char in _INVALID_NAME_TOKENS:
            n = n.replace(char, "")
        if len(n) < 4:
            return None
        if re.match(r"^\d+$", n):
            return None
        if "机器编号" in n or "税务局" in n:
            return None
        return n

    def validate(self, value: str) -> bool:
        return bool(value) and len(value) >= 4

    def fallback(self, text: str, filename: str) -> Optional[str]:
        candidates = re.findall(r"([\u4e00-\u9fa5()（）]{4,20}公司)", text)
        for cand in candidates:
            if self.validate(cand):
                return cand
        return None
