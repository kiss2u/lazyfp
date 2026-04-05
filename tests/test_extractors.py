import pytest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rule_engine import RuleEngine
from core.cache import InvoiceCache
from core.queue_manager import UploadQueue, TaskStatus
from extractors.invoice_no import InvoiceNoExtractor
from extractors.date_extractor import DateExtractor
from extractors.amount_extractor import AmountExtractor
from extractors.company_name import CompanyNameExtractor


class TestRuleEngine:
    def test_load_builtin_rules(self):
        engine = RuleEngine("config/rules.json", "config/custom_rules.json")
        inv_rules = engine.get_rules("invoice_no")
        assert "primary" in inv_rules
        assert "fallback" in inv_rules
        assert len(inv_rules["primary"]) > 0

    def test_custom_rules_priority(self):
        engine = RuleEngine("config/rules.json", "config/custom_rules.json")
        date_rules = engine.get_rules("date")
        fallback_len_before = len(date_rules["fallback"])

        engine.add_rule("date", {"pattern": "test", "flags": 0, "desc": "test"})
        date_rules_after = engine.get_rules("date")
        assert len(date_rules_after["fallback"]) == fallback_len_before + 1

    def test_reload(self):
        engine = RuleEngine("config/rules.json", "config/custom_rules.json")
        engine.reload()
        assert engine.get_rules("amount") is not None


class TestInvoiceNoExtractor:
    def setup_method(self):
        rules = [
            {"pattern": r"发票号码[:：]?\s*(\d{20}|\d{8,12})", "flags": 0, "desc": "test"},
            {"pattern": r"\b\d{20}\b", "flags": 0, "desc": "20digit"},
        ]
        self.ext = InvoiceNoExtractor(rules)

    def test_extract_20_digit(self):
        text = "发票号码:25312000000327776462 some other text"
        result = self.ext.extract(text)
        assert result == "25312000000327776462"

    def test_extract_8_digit(self):
        text = "发票号码:12345678"
        result = self.ext.extract(text)
        assert result == "12345678"

    def test_extract_no_match(self):
        text = "no invoice number here"
        result = self.ext.extract(text)
        assert result is None

    def test_validate_rejects_short(self):
        assert not self.ext.validate("1234567")

    def test_validate_rejects_non_digit(self):
        assert not self.ext.validate("abc12345")


class TestDateExtractor:
    def setup_method(self):
        rules = [
            {"pattern": r"(20\d{2}年\d{1,2}月\d{1,2}日)", "flags": 0, "desc": "chinese"},
            {"pattern": r"(\d{4}-\d{2}-\d{2})", "flags": 0, "desc": "iso"},
        ]
        self.ext = DateExtractor(rules)

    def test_extract_chinese_date(self):
        text = "开票日期 2023年05月15日"
        result = self.ext.extract(text)
        assert result == "2023年05月15日"

    def test_extract_iso_date(self):
        text = "date: 2023-05-15"
        result = self.ext.extract(text)
        assert result == "2023-05-15"

    def test_parse_quarter_q1(self):
        assert self.ext.parse_quarter("2023年01月15日") == "2023-Q1"

    def test_parse_quarter_q2(self):
        assert self.ext.parse_quarter("2023年05月15日") == "2023-Q2"

    def test_parse_quarter_q3(self):
        assert self.ext.parse_quarter("2023年08月15日") == "2023-Q3"

    def test_parse_quarter_q4(self):
        assert self.ext.parse_quarter("2023年12月15日") == "2023-Q4"

    def test_parse_quarter_unknown(self):
        assert self.ext.parse_quarter("invalid") == "Unknown"


class TestAmountExtractor:
    def setup_method(self):
        rules = [
            {"pattern": r"小\s*写.*?[¥￥]?\s*([\d,]+\.?\d*)", "flags": 0, "desc": "lowercase"},
        ]
        self.ext = AmountExtractor(rules)

    def test_extract_amount(self):
        text = "小写 ¥1,234.56"
        result = self.ext.extract(text)
        assert result == "1234.56"

    def test_extract_amount_no_symbol(self):
        text = "小写 999.99"
        result = self.ext.extract(text)
        assert result == "999.99"

    def test_validate_range(self):
        assert self.ext.validate("1000.00")
        assert not self.ext.validate("999999999")
        assert not self.ext.validate("abc")


class TestCompanyNameExtractor:
    def setup_method(self):
        rules = [
            {"pattern": r"(?:购)?名称[:：](.+?)(?:销|售|纳税)", "flags": 0, "desc": "purchaser"},
        ]
        self.ext = CompanyNameExtractor(rules, "purchaser")

    def test_extract_company(self):
        text = "名称:北京科技有限公司销售方"
        result = self.ext.extract(text)
        assert result == "北京科技有限公司"

    def test_clean_removes_artifacts(self):
        result = self.ext.clean("购买方名称")
        assert result is None

    def test_validate_rejects_short(self):
        assert not self.ext.validate("公司")

    def test_fallback_company(self):
        text = "some random 上海某某有限公司 text"
        result = self.ext.fallback(text, "")
        assert result == "上海某某有限公司"


class TestInvoiceCache:
    def test_get_put(self, tmp_path):
        cache_file = str(tmp_path / "test_cache.json")
        cache = InvoiceCache(cache_file)
        cache.put("test.pdf", 1000.0, 5000, {"invoice_no": "123"})
        result = cache.get("test.pdf", 1000.0, 5000)
        assert result == {"invoice_no": "123"}

    def test_cache_miss_on_mtime_change(self, tmp_path):
        cache_file = str(tmp_path / "test_cache.json")
        cache = InvoiceCache(cache_file)
        cache.put("test.pdf", 1000.0, 5000, {"invoice_no": "123"})
        result = cache.get("test.pdf", 2000.0, 5000)
        assert result is None

    def test_atomic_save(self, tmp_path):
        cache_file = str(tmp_path / "test_cache.json")
        cache = InvoiceCache(cache_file)
        cache.put("test.pdf", 1000.0, 5000, {"invoice_no": "123"})
        cache.save()
        assert os.path.exists(cache_file)
        assert not os.path.exists(cache_file + ".tmp")

    def test_cleanup(self, tmp_path):
        cache_file = str(tmp_path / "test_cache.json")
        cache = InvoiceCache(cache_file)
        cache.put("a.pdf", 1000.0, 5000, {"data": {}})
        cache.put("b.pdf", 1000.0, 5000, {"data": {}})
        cache.cleanup({"a.pdf"})
        assert cache.get("a.pdf", 1000.0, 5000) is not None
        assert cache.get("b.pdf", 1000.0, 5000) is None

    def test_invalidate_single(self, tmp_path):
        cache_file = str(tmp_path / "test_cache.json")
        cache = InvoiceCache(cache_file)
        cache.put("test.pdf", 1000.0, 5000, {"data": {}})
        cache.invalidate("test.pdf")
        assert cache.get("test.pdf", 1000.0, 5000) is None

    def test_invalidate_all(self, tmp_path):
        cache_file = str(tmp_path / "test_cache.json")
        cache = InvoiceCache(cache_file)
        cache.put("a.pdf", 1000.0, 5000, {"data": {}})
        cache.put("b.pdf", 1000.0, 5000, {"data": {}})
        cache.invalidate()
        assert cache.get("a.pdf", 1000.0, 5000) is None
        assert cache.get("b.pdf", 1000.0, 5000) is None
