import os
import re
import logging
from typing import Optional
import pdfplumber
from extractors.invoice_no import InvoiceNoExtractor
from extractors.date_extractor import DateExtractor
from extractors.amount_extractor import AmountExtractor
from extractors.company_name import CompanyNameExtractor
from core.rule_engine import RuleEngine

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, rule_engine: RuleEngine):
        self._rule_engine = rule_engine
        self._init_extractors()

    def _init_extractors(self):
        inv_rules = self._rule_engine.get_rules("invoice_no")
        date_rules = self._rule_engine.get_rules("date")
        amt_rules = self._rule_engine.get_rules("amount")
        purch_rules = self._rule_engine.get_rules("purchaser")
        seller_rules = self._rule_engine.get_rules("seller")

        all_purch_rules = purch_rules["primary"] + purch_rules["fallback"]
        all_seller_rules = seller_rules["primary"] + seller_rules["fallback"]

        self._invoice_no_ext = InvoiceNoExtractor(
            inv_rules["primary"] + inv_rules["fallback"]
        )
        self._date_ext = DateExtractor(
            date_rules["primary"] + date_rules["fallback"]
        )
        self._amount_ext = AmountExtractor(
            amt_rules["primary"] + amt_rules["fallback"]
        )
        self._purchaser_ext = CompanyNameExtractor(all_purch_rules, "purchaser")
        self._seller_ext = CompanyNameExtractor(all_seller_rules, "seller")

    def extract(self, pdf_path: str) -> dict:
        filename = os.path.basename(pdf_path)
        data = {
            "invoice_no": None,
            "date": None,
            "purchaser": None,
            "seller": None,
            "total_amount": None,
            "filename": filename,
            "raw_text": None,
            "error": None,
            "maybe_not_invoice": False,
        }

        try:
            with pdfplumber.open(pdf_path) as pdf:
                if not pdf.pages:
                    logger.warning("File %s has no pages.", filename)
                    return data

                page = pdf.pages[0]
                text = page.extract_text() or ""

                if not text:
                    logger.warning("File %s has no extractable text.", filename)
                    data["raw_text"] = ""
                    data["maybe_not_invoice"] = True
                    return data

                data["raw_text"] = text

                if not self._looks_like_invoice(text):
                    data["maybe_not_invoice"] = True
                text_flat = re.sub(r"\(cid:\d+\)", "", text)
                text_flat = re.sub(r"[\s\u3000\xa0]+", "", text_flat)
                text_clean = re.sub(r"\(cid:\d+\)", "：", text)

                data["invoice_no"] = self._invoice_no_ext.extract(text_flat, filename)
                if not data["invoice_no"]:
                    data["invoice_no"] = self._invoice_no_ext.extract(text, filename)

                data["date"] = self._date_ext.extract(text_flat)
                if not data["date"]:
                    data["date"] = self._date_ext.extract(text)

                data["total_amount"] = self._amount_ext.extract(text_flat)
                if data["total_amount"] is not None:
                    data["total_amount"] = float(data["total_amount"])

                data["purchaser"] = self._purchaser_ext.extract(text_flat)
                if not data["purchaser"]:
                    data["purchaser"] = self._purchaser_ext.extract(text)

                data["seller"] = self._seller_ext.extract(text_flat)
                if not data["seller"]:
                    data["seller"] = self._seller_ext.extract(text)

                if not data["purchaser"] or not data["seller"]:
                    spatial_p, spatial_s = self._spatial_extract(page)
                    if not data["purchaser"] and spatial_p:
                        data["purchaser"] = spatial_p
                    if not data["seller"] and spatial_s:
                        data["seller"] = spatial_s

                if not data["purchaser"] or not data["seller"]:
                    table_p, table_s = self._table_fallback(page)
                    if not data["purchaser"] and table_p:
                        data["purchaser"] = table_p
                    if not data["seller"] and table_s:
                        data["seller"] = table_s

                for field_name in ["purchaser", "seller"]:
                    val = data.get(field_name)
                    if val and ("税务" in val or "方方" in val or "购销" in val or "信信" in val or len(val) > 30):
                        data[field_name] = None

                if not data["purchaser"] or not data["seller"]:
                    table_p, table_s = self._table_fallback(page)
                    if not data["purchaser"] and table_p:
                        data["purchaser"] = table_p
                    if not data["seller"] and table_s:
                        data["seller"] = table_s

                if not data["invoice_no"]:
                    data["invoice_no"] = self._extract_invoice_no_from_stamp(text_flat)

                if not data["date"]:
                    data["date"] = self._date_ext.fallback(text, filename)

                if data["date"]:
                    data["date"] = self._date_ext.clean(data["date"])
                    if not self._date_ext.validate(data["date"]):
                        data["date"] = None

                p = data.get("purchaser") or ""
                s = data.get("seller") or ""
                if p and s and (p == s or p in s or s in p):
                    logger.warning("Purchaser and seller overlap for %s. Clearing and re-extracting.", filename)
                    data["purchaser"] = None
                    data["seller"] = None
                    table_p, table_s = self._table_fallback(page)
                    if table_p:
                        data["purchaser"] = table_p
                    if table_s and table_p and table_s != table_p and table_p not in table_s and table_s not in table_p:
                        data["seller"] = table_s

                if not data.get("seller") or not data.get("purchaser"):
                    bottom_p, bottom_s = self._extract_from_bottom_text(text)
                    if not data.get("purchaser") and bottom_p:
                        data["purchaser"] = bottom_p
                    current_seller = data.get("seller") or ""
                    if bottom_s and bottom_s != current_seller and current_seller not in bottom_s:
                        data["seller"] = bottom_s

                p = data.get("purchaser") or ""
                s = data.get("seller") or ""
                if p and s and (p == s or p in s or s in p):
                    data["seller"] = None

        except Exception as e:
            logger.error("Critical error parsing %s: %s", filename, e)
            data["error"] = str(e)

        return data

    def _spatial_extract(self, page):
        width, height = page.width, page.height
        purchaser = None
        seller = None

        left_box = (0, height * 0.15, width * 0.55, height * 0.60)
        left_text = page.within_bbox(left_box).extract_text()
        if left_text:
            cand = re.search(r"([^\n]{2,30}公司)", left_text)
            if cand:
                c = cand.group(1).strip()
                if "税务局" not in c and "税务" not in c:
                    purchaser = c

        right_box = (width * 0.45, height * 0.15, width, height * 0.60)
        right_text = page.within_bbox(right_box).extract_text() or ""
        if right_text:
            cand = re.search(r"([^\n]{2,30}公司)", right_text)
            if cand:
                c = cand.group(1).strip()
                if "税务局" not in c and "税务" not in c:
                    if not purchaser or c not in purchaser:
                        seller = c

        if not seller:
            bottom_box = (0, height * 0.60, width, height * 0.95)
            bot_text = page.within_bbox(bottom_box).extract_text() or ""
            for m in re.finditer(r"([^\n]{4,30}公司)", bot_text):
                c = m.group(1).strip()
                if "税务局" in c or "税务" in c:
                    continue
                if purchaser and c in purchaser:
                    continue
                if "咨询" in c and purchaser and "咨询" in purchaser:
                    continue
                seller = c
                break

        return purchaser, seller

    def _table_fallback(self, page):
        tables = page.extract_tables()
        if not tables:
            return None, None

        purchaser = None
        seller = None
        all_cell_texts = []

        for table in tables:
            for row in table:
                for k, cell in enumerate(row):
                    if not cell:
                        continue
                    cell_text = str(cell).strip()
                    all_cell_texts.append(cell_text)

                    if "销售方" in cell_text or "销" in cell_text.split("名称")[0] if "名称" in cell_text else False:
                        m = re.search(r"名称[:：]\s*([\u4e00-\u9fa5()（）]{2,50})", cell_text)
                        if m:
                            name = m.group(1).strip()
                            if name and "税务局" not in name and "税务" not in name and len(name) >= 4:
                                if not seller:
                                    seller = name
                    elif "购买方" in cell_text or "购" in cell_text.split("名称")[0] if "名称" in cell_text else False:
                        m = re.search(r"名称[:：]\s*([\u4e00-\u9fa5()（）]{2,50})", cell_text)
                        if m:
                            name = m.group(1).strip()
                            if name and "税务局" not in name and "税务" not in name and len(name) >= 4:
                                if not purchaser:
                                    purchaser = name

                if purchaser and seller:
                    return purchaser, seller

        if not purchaser or not seller:
            for cell_text in all_cell_texts:
                names_in_cell = re.findall(r"名称[:：]\s*([\u4e00-\u9fa5()（）]{2,50}?)\s*(?:统一|信用|纳税|注册|地址|开户|电话|银行|账号|$)", cell_text)
                for name in names_in_cell:
                    name = name.strip()
                    if not name or len(name) < 4:
                        continue
                    if "税务局" in name or "税务" in name:
                        continue
                    if not purchaser:
                        purchaser = name
                    elif name != purchaser and not seller:
                        seller = name
                        break
                if purchaser and seller:
                    break

        if purchaser and not seller:
            for cell_text in all_cell_texts:
                if purchaser in cell_text:
                    continue
                names_in_cell = re.findall(r"名称[:：]\s*([\u4e00-\u9fa5()（）]{2,50}?)\s*(?:统一|信用|纳税|注册|地址|开户|电话|银行|账号|$)", cell_text)
                for name in names_in_cell:
                    name = name.strip()
                    if not name or len(name) < 4:
                        continue
                    if "税务局" in name or "税务" in name:
                        continue
                    seller = name
                    break
                if seller:
                    break

        return purchaser, seller

    def _extract_from_bottom_text(self, text: str) -> tuple:
        lines = text.split('\n')
        purchaser_name = None
        seller_name = None
        for line in lines:
            if re.search(r"[购买]\s*名\s*称", line):
                m = re.search(r"称[:：]\s*([\u4e00-\u9fa5()（）]{2,50})", line)
                if m:
                    name = m.group(1).strip()
                    if name and "税务局" not in name and "税务" not in name and len(name) >= 4:
                        if not purchaser_name:
                            purchaser_name = name
            elif re.search(r"[销]\s*名\s*称", line) or re.search(r"销\s*售", line):
                m = re.search(r"称[:：]\s*([\u4e00-\u9fa5()（）]{2,50})", line)
                if m:
                    name = m.group(1).strip()
                    if name and "税务局" not in name and "税务" not in name and len(name) >= 4:
                        if not seller_name:
                            seller_name = name
            elif '称' in line and '：' in line and '购' not in line.split('称')[0]:
                m = re.search(r"称[:：]\s*([\u4e00-\u9fa5()（）]{2,50})", line)
                if m:
                    name = m.group(1).strip()
                    if name and "税务局" not in name and "税务" not in name and len(name) >= 4:
                        if not purchaser_name:
                            purchaser_name = name
                        elif not seller_name and name != purchaser_name:
                            seller_name = name
        return purchaser_name, seller_name

    def _looks_like_invoice(self, text: str) -> bool:
        invoice_keywords = [
            "发票", "发票号码", "开票日期", "价税合计", "小写",
            "购买方", "销售方", "购方", "销方", "税额", "税率",
            "发票代码", "校验码", "机器编号",
            "电子发票", "普通发票", "专用发票",
            "发票专用章", "国家税务总局",
            "对账单", "中国移动通信",
        ]
        text_lower = text
        matches = sum(1 for kw in invoice_keywords if kw in text_lower)
        return matches >= 2

    def _extract_invoice_no_from_stamp(self, text_flat: str) -> Optional[str]:
        m = re.search(r"监制\s*(\d{20})", text_flat)
        if m:
            return m.group(1)
        m = re.search(r"(\d{20})\s*全章", text_flat)
        if m:
            return m.group(1)
        m = re.search(r"(\d{20})", text_flat)
        if m:
            return m.group(1)
        return None

    def get_quarter(self, date_str: str) -> str:
        return self._date_ext.parse_quarter(date_str)
