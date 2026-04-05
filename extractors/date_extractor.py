import re
from datetime import datetime
from typing import Optional
from extractors.base import BaseExtractor


class DateExtractor(BaseExtractor):
    def clean(self, value: str) -> str:
        value = value.strip()
        m = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", value)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        value = value.replace("/", "-").replace(".", "-")
        if " " in value:
            parts = value.split()
            if len(parts) == 3:
                try:
                    dt = datetime.strptime(f"{parts[0]}-{parts[1]}-{parts[2]}", "%Y-%m-%d")
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    pass
        return value

    def validate(self, value: str) -> bool:
        if not re.search(r"\d{4}", value):
            return False
        if len(value) < 6:
            return False
        if len(value) > 20:
            return False
        return True

    def fallback(self, text: str, filename: str) -> Optional[str]:
        all_digits = "".join(re.findall(r"\d", text))
        matches = re.finditer(r"(20[23]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])", all_digits)
        for m in matches:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        return None

    def parse_quarter(self, date_str: str) -> str:
        if not date_str:
            return "Unknown"
        try:
            ds = date_str.replace("/", "-").replace(".", "-")
            if '年' in ds:
                dt = datetime.strptime(ds, "%Y年%m月%d日")
            else:
                dt = datetime.strptime(ds, "%Y-%m-%d")
            quarter = (dt.month - 1) // 3 + 1
            return f"{dt.year}-Q{quarter}"
        except (ValueError, TypeError):
            match = re.search(r"(\d{4})[-\u5e74](\d{1,2})[-\u6708](\d{1,2})", date_str)
            if match:
                try:
                    y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
                    quarter = (m - 1) // 3 + 1
                    return f"{y}-Q{quarter}"
                except (ValueError, TypeError):
                    pass
            return "Unknown"
