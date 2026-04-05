import json
import os
from typing import Optional


class RuleEngine:
    def __init__(self, rules_file: str, custom_rules_file: str):
        self._rules_file = rules_file
        self._custom_rules_file = custom_rules_file
        self._rules: dict = {}
        self._load()

    def _load(self):
        builtin = self._load_json(self._rules_file) or {}
        custom = self._load_json(self._custom_rules_file) or {}
        self._rules = self._merge(builtin, custom)

    def _load_json(self, path: str) -> Optional[dict]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def _normalize(self, rules) -> dict:
        if isinstance(rules, list):
            return {"primary": [], "fallback": rules}
        if isinstance(rules, dict):
            return {
                "primary": rules.get("primary", []),
                "fallback": rules.get("fallback", []),
            }
        return {"primary": [], "fallback": []}

    def _merge(self, builtin: dict, custom: dict) -> dict:
        merged = {}
        all_fields = set(builtin.keys()) | set(custom.keys())
        for field in all_fields:
            b_rules = builtin.get(field, {})
            c_rules = self._normalize(custom.get(field, {}))
            b_normalized = self._normalize(b_rules) if isinstance(b_rules, dict) else {"primary": [], "fallback": []}
            merged[field] = {
                "primary": c_rules.get("primary", []) + b_normalized.get("primary", []),
                "fallback": c_rules.get("fallback", []) + b_normalized.get("fallback", []),
            }
        return merged

    def get_rules(self, field: str) -> dict:
        return self._rules.get(field, {"primary": [], "fallback": []})

    def add_rule(self, field: str, rule: dict):
        custom = self._load_json(self._custom_rules_file) or {}
        existing = custom.get(field, [])
        if isinstance(existing, list):
            custom[field] = {"primary": [], "fallback": existing}
        elif isinstance(existing, dict):
            if "fallback" not in existing:
                existing["fallback"] = []
            if "primary" not in existing:
                existing["primary"] = []
        else:
            custom[field] = {"primary": [], "fallback": []}
        custom[field]["fallback"].append(rule)
        tmp = self._custom_rules_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(custom, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._custom_rules_file)
        except IOError:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        self._load()

    def reload(self):
        self._load()
