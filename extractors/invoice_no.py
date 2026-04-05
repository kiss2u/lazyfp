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
        m1 = re.search(r"客户账号\s*(\d+)", text)
        m2 = re.search(r"集团编号\s*(\d+)", text)
        m3 = re.search(r"打印日期\s*(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", text)
        if m1 and m2 and m3:
            account = m1.group(1)
            group_no = m2.group(1)
            date_key = m3.group(1) + m3.group(2).zfill(2) + m3.group(3).zfill(2)
            return account + group_no + date_key
        nums_8 = re.findall(r"\b(\d{8})\b", text)
        for n in nums_8:
            if n.startswith("202"):
                continue
            return n
        return None
