"""Field-level three-way import comparison, without an LLM or filesystem IO."""

from __future__ import annotations

import copy
from typing import Any


_MISSING = object()
_ID_FIELDS = ("component_id", "choice_id", "branch_id", "effect_id", "frame_id", "asset_id", "character_id", "act_id", "variable_id", "game_id")


def _same(left: Any, right: Any) -> bool:
    if left is _MISSING or right is _MISSING:
        return left is right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return set(left) == set(right) and all(_same(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _copy(value: Any) -> Any:
    return _MISSING if value is _MISSING else copy.deepcopy(value)


def _visible(value: Any) -> Any:
    return None if value is _MISSING else copy.deepcopy(value)


def _path(parent: str, key: str) -> str:
    return parent + "/" + key.replace("~", "~0").replace("/", "~1")


def _change(path: str, before: Any, after: Any, origin: str) -> dict[str, Any]:
    return {
        "path": path, "action": "add" if before is _MISSING else "remove" if after is _MISSING else "replace",
        "before_exists": before is not _MISSING, "after_exists": after is not _MISSING,
        "before": _visible(before), "after": _visible(after), "origin": origin,
    }


def _conflict(path: str, baseline: Any, current: Any, incoming: Any) -> dict[str, Any]:
    return {
        "path": path,
        "baseline_exists": baseline is not _MISSING,
        "current_exists": current is not _MISSING,
        "incoming_exists": incoming is not _MISSING,
        "baseline": _visible(baseline), "current": _visible(current), "incoming": _visible(incoming),
    }


def _list_identity(*values: list[Any]) -> str | None:
    items = [item for value in values for item in value]
    if not items or any(not isinstance(item, dict) for item in items):
        return None
    for field in _ID_FIELDS:
        if all(isinstance(item.get(field), str) and item[field] for item in items):
            return field
    return None


def _index_list(items: list[dict[str, Any]], field: str, path: str) -> tuple[list[str], dict[str, Any]]:
    ids = [item[field] for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"IDENTITY_CONFLICT: {path} 包含重复 {field}")
    return ids, {item[field]: item for item in items}


def _combine_order(primary: list[str], secondary: list[str], present: set[str]) -> list[str]:
    order = [key for key in primary if key in present]
    for index, key in enumerate(secondary):
        if key not in present or key in order:
            continue
        previous = next((candidate for candidate in reversed(secondary[:index]) if candidate in order), None)
        following = next((candidate for candidate in secondary[index + 1:] if candidate in order), None)
        if previous is not None:
            order.insert(order.index(previous) + 1, key)
        elif following is not None:
            order.insert(order.index(following), key)
        else:
            order.append(key)
    order.extend(sorted(present - set(order)))
    return order


class _Comparison:
    def __init__(self):
        self.changes: list[dict[str, Any]] = []
        self.conflicts: list[dict[str, Any]] = []

    def merge(self, baseline: Any, current: Any, incoming: Any, path: str) -> Any:
        # Layout is decided by AI unless new source explicitly supplies it.
        # An absent layout inside a retained object does not mean delete it.
        if path.rsplit("/", 1)[-1] == "layout" and incoming is _MISSING:
            return _copy(current)

        if all(isinstance(value, dict) for value in (baseline, current, incoming)):
            merged = {}
            for key in sorted(set(baseline) | set(current) | set(incoming)):
                if not isinstance(key, str):
                    raise ValueError("导入对象的 key 必须是字符串")
                result = self.merge(baseline.get(key, _MISSING), current.get(key, _MISSING), incoming.get(key, _MISSING), _path(path, key))
                if result is not _MISSING:
                    merged[key] = result
            return merged

        if all(isinstance(value, list) for value in (baseline, current, incoming)):
            identity = _list_identity(baseline, current, incoming)
            if identity:
                return self.merge_identified_list(baseline, current, incoming, path, identity)

        if _same(current, incoming):
            result, origin = _copy(current), "both"
        elif _same(current, baseline):
            result, origin = _copy(incoming), "source"
        elif _same(incoming, baseline):
            result, origin = _copy(current), "current"
        else:
            self.conflicts.append(_conflict(path, baseline, current, incoming))
            return _copy(current)
        if not _same(result, current):
            self.changes.append(_change(path, current, result, origin))
        return result

    def merge_identified_list(self, baseline: list, current: list, incoming: list, path: str, identity: str) -> list:
        baseline_ids, baseline_index = _index_list(baseline, identity, path)
        current_ids, current_index = _index_list(current, identity, path)
        incoming_ids, incoming_index = _index_list(incoming, identity, path)
        results = {}
        for key in sorted(set(baseline_index) | set(current_index) | set(incoming_index)):
            value = self.merge(baseline_index.get(key, _MISSING), current_index.get(key, _MISSING), incoming_index.get(key, _MISSING), _path(path, key))
            if value is not _MISSING:
                results[key] = value

        common = set(baseline_ids) & set(current_ids) & set(incoming_ids)
        baseline_order = [key for key in baseline_ids if key in common]
        current_order = [key for key in current_ids if key in common]
        incoming_order = [key for key in incoming_ids if key in common]
        current_reordered = current_order != baseline_order
        source_reordered = incoming_order != baseline_order
        if current_reordered and source_reordered and current_order != incoming_order:
            self.conflicts.append(_conflict(_path(path, "$order"), baseline_ids, current_ids, incoming_ids))
            preferred, secondary = current_ids, incoming_ids
        elif current_reordered:
            preferred, secondary = current_ids, incoming_ids
        else:
            preferred, secondary = incoming_ids, current_ids
        order = _combine_order(preferred, secondary, set(results))
        # Pure reordering has no changed leaves, so explicitly report it.
        surviving_current = [key for key in current_ids if key in results]
        if order != surviving_current:
            self.changes.append(_change(_path(path, "$order"), current_ids, order, "source"))
        return [results[key] for key in order]


def compare_import(baseline: dict, current: dict, incoming: dict) -> dict[str, Any]:
    """Return a candidate, applicable changes and explicit unresolved conflicts.

    Paths escape dictionary keys like JSON Pointer. For identified lists, a
    segment addresses the stable ID instead of a numeric position; $order
    addresses order. The result is a plan, not a validated Project/commit.
    """
    if not all(isinstance(value, dict) for value in (baseline, current, incoming)):
        raise ValueError("三方导入需要 baseline/current/incoming 对象")
    comparison = _Comparison()
    merged = comparison.merge(baseline, current, incoming, "")
    return {"merged": merged, "changes": comparison.changes, "conflicts": comparison.conflicts}


def _tokens(path: str) -> list[str]:
    if not path.startswith("/"):
        raise ValueError("冲突路径必须以 / 开头")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _list_field(items: list[Any]) -> str:
    field = _list_identity(items)
    if field is None:
        raise ValueError("该路径不能以稳定 ID 定位列表")
    return field


def _find_item(items: list, token: str) -> int:
    field = _list_field(items)
    for index, item in enumerate(items):
        if item[field] == token:
            return index
    raise ValueError(f"当前候选列表缺少对象 {token}")


def _apply_resolution(root: dict, path: str, value: Any, exists: bool) -> None:
    tokens = _tokens(path)
    container: Any = root
    for token in tokens[:-1]:
        container = container[_find_item(container, token)] if isinstance(container, list) else container[token]
    token = tokens[-1]
    if isinstance(container, list):
        if token == "$order":
            if not exists or not isinstance(value, list):
                raise ValueError("列表顺序解决值必须是 ID 列表")
            if not container:
                return
            field = _list_field(container)
            previous_ids, indexed = _index_list(container, field, path)
            order = _combine_order(value, previous_ids, set(indexed))
            container[:] = [indexed[key] for key in order]
            return
        if not container:
            if exists and isinstance(value, dict) and any(value.get(field) == token for field in _ID_FIELDS):
                container.append(copy.deepcopy(value))
                return
            if not exists:
                return
            raise ValueError("空列表中新增对象需要稳定 ID")
        field = _list_field(container)
        matching = next((index for index, item in enumerate(container) if item[field] == token), None)
        if exists:
            if not isinstance(value, dict) or value.get(field) != token:
                raise ValueError("解决对象与稳定列表 ID 不一致")
            if matching is None:
                container.append(copy.deepcopy(value))
            else:
                container[matching] = copy.deepcopy(value)
        elif matching is not None:
            container.pop(matching)
    elif isinstance(container, dict):
        if exists:
            container[token] = copy.deepcopy(value)
        else:
            container.pop(token, None)
    else:
        raise ValueError("冲突路径不能定位候选对象")


def resolve_import(plan: dict, resolutions: dict[str, str]) -> dict[str, Any]:
    """Resolve some/all conflicts without modifying the plan or inputs.

    Store may only commit after conflicts=[] and full Project/reference checks.
    """
    if not isinstance(plan, dict) or not isinstance(resolutions, dict):
        raise ValueError("导入计划和解决选择必须是对象")
    result = copy.deepcopy(plan)
    conflicts = result.get("conflicts", [])
    by_path = {conflict["path"]: conflict for conflict in conflicts}
    unknown = set(resolutions) - set(by_path)
    if unknown:
        raise ValueError(f"未知冲突路径: {sorted(unknown)}")
    for path, selection in resolutions.items():
        if selection not in ("source", "current"):
            raise ValueError("冲突选择只允许 source/current")
        conflict = by_path[path]
        field = "incoming" if selection == "source" else "current"
        exists = conflict[field + "_exists"]
        value = conflict[field]
        _apply_resolution(result["merged"], path, value, exists)
        if selection == "source":
            before = conflict["current"] if conflict["current_exists"] else _MISSING
            after = value if exists else _MISSING
            if not _same(before, after):
                result["changes"].append(_change(path, before, after, "resolved_source"))
    result["conflicts"] = [conflict for conflict in conflicts if conflict["path"] not in resolutions]
    return result
