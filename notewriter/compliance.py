"""コンプライアンスチェッカー（SPEC.md「作ってほしい機能 3.」）。

下書き（Markdown）を読み、禁句・個別助言に読める言い回し・ぼかすべき属性・属性の集中・
統計の出典/年度・伏せ字・曜日の誤り・シリーズ内の矛盾（生年）を検出して「警告」を返す。

**本文は絶対に変更しない**（SPEC.md「絶対に守ってほしいこと 3.」）。どの関数もテキストを返さず、
Finding の一覧だけを返す。直すかどうか・どう直すかは人間が判断する。

ルールは編集可能な JSON:
- 既定: リポジトリの config/compliance_rules.json
- 上書き: 作業フォルダ/config/compliance_rules.json（あれば。キー単位で置き換え、private_terms だけは結合）
作業フォルダが未初期化・未マウントでも既定ルールだけで動く（vault() は呼ばない）。
"""
from __future__ import annotations

import bisect
import datetime as _dt
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import store

RULES_FILE = "compliance_rules.json"

CATEGORIES = ("伏せ字", "禁句", "助言表現", "属性", "属性の集中", "出典", "年度", "曜日", "シリーズ矛盾")
SEVERITIES = ("strong", "warn", "info")
DEFAULT_CLUSTER_THRESHOLD = 3

KNOWN_KEYS = {
    "forbidden_words", "advice_patterns", "attributes", "attribute_cluster_threshold",
    "attribute_cluster_note", "statistics", "private_terms", "private_terms_note", "ignore_patterns",
}


@dataclass
class Finding:
    """1件の警告。start/end は行内の文字位置（0始まり、end は含まない）。位置がないときは -1。"""
    category: str
    severity: str
    doc: str
    line: int
    match: str
    excerpt: str
    message: str
    rule: str
    start: int = -1
    end: int = -1

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- ルールの読み込み

def default_rules_path() -> Path:
    return store.CONFIG_DIR / RULES_FILE


def override_rules_path() -> Path:
    return store.data_dir() / "config" / RULES_FILE


def _read_rules_file(path: Path, errors: List[str]) -> Optional[dict]:
    try:
        data = store.read_json(path, None)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        errors.append(f"{path}: ルールファイルを読み込めません（{e}）")
        return None
    if data is None:
        return None
    if not isinstance(data, dict):
        errors.append(f"{path}: ルールファイルの中身が JSON のオブジェクト（{{...}}）ではありません")
        return None
    return data


def load_rules() -> dict:
    """既定ルールに作業フォルダ側の上書きを結合して返す。

    上書きはトップレベルのキー単位で置き換える（`_` で始まる説明用キーは無視）。
    private_terms だけは既定と結合する。読み込んだファイルは rules["_sources"]、
    読み込みの問題は rules["_errors"] に入る（validate_rules でも報告される）。
    """
    errors: List[str] = []
    sources: List[str] = []
    base_path = default_rules_path()
    rules = _read_rules_file(base_path, errors)
    if rules is None:
        if not errors:
            errors.append(f"{base_path}: 既定のルールファイルがありません（空のルールで動きます）")
        rules = {}
    else:
        sources.append(str(base_path))

    ov_path = override_rules_path()
    if ov_path.is_file():
        ov = _read_rules_file(ov_path, errors)
        if ov is not None:
            sources.append(str(ov_path))
            for k, v in ov.items():
                if k.startswith("_"):
                    continue
                if k == "private_terms":
                    if not isinstance(v, list):
                        errors.append(f"{ov_path}: private_terms はリストで書いてください")
                        continue
                    merged = list(rules.get("private_terms") or [])
                    for item in v:
                        if item not in merged:
                            merged.append(item)
                    rules[k] = merged
                else:
                    rules[k] = v
    rules["_sources"] = sources
    rules["_errors"] = errors
    return rules


# ---------------------------------------------------------------- ルールの検証

def _as_items(section, key: str) -> list:
    """文字列だけの項目も {key: 文字列} に揃える。リストでなければ空。"""
    if not isinstance(section, list):
        return []
    out = []
    for item in section:
        if isinstance(item, str):
            out.append({key: item})
        elif isinstance(item, dict):
            out.append(item)
        else:
            out.append({})
    return out


def _attr_spec(spec) -> dict:
    if isinstance(spec, list):
        return {"patterns": spec}
    return spec if isinstance(spec, dict) else {}


def validate_rules(rules: dict) -> List[str]:
    """ルールの問題（正規表現のエラー・型の誤り・不明なキー等）を日本語で列挙する。問題がなければ空リスト。"""
    problems: List[str] = list(rules.get("_errors") or [])

    def regex(pat, where: str) -> None:
        if not isinstance(pat, str) or not pat:
            problems.append(f"{where}: 正規表現が空か、文字列ではありません")
            return
        try:
            rx = re.compile(pat)
        except re.error as e:
            problems.append(f"{where}: 正規表現のエラー（{e}）: {pat}")
            return
        if rx.search("") is not None:
            problems.append(f"{where}: 空文字列にも一致する正規表現なので使えません: {pat}")

    def pattern_list(lst, where: str) -> None:
        if not isinstance(lst, list):
            problems.append(f"{where}: 正規表現のリストで書いてください")
            return
        for i, p in enumerate(lst):
            regex(p, f"{where}[{i}]")

    def severity(item: dict, where: str) -> None:
        sev = item.get("severity")
        if sev is not None and sev not in SEVERITIES:
            problems.append(f"{where}: severity は {' / '.join(SEVERITIES)} のどれかにしてください（{sev}）")

    for k in rules:
        if not k.startswith("_") and k not in KNOWN_KEYS:
            problems.append(f"{k}: 不明なキーです（無視されます。綴りを確認してください）")

    if "forbidden_words" in rules:
        if not isinstance(rules["forbidden_words"], list):
            problems.append("forbidden_words: リストで書いてください")
        for i, item in enumerate(_as_items(rules.get("forbidden_words"), "word")):
            where = f"forbidden_words[{i}]"
            if not isinstance(item.get("word"), str) or not item.get("word"):
                problems.append(f"{where}: word（禁句）が空か、文字列ではありません")
            exc = item.get("except", [])
            if not isinstance(exc, list) or not all(isinstance(x, str) and x for x in exc):
                problems.append(f"{where}: except は文字列のリストで書いてください")
            severity(item, where)

    if "advice_patterns" in rules:
        if not isinstance(rules["advice_patterns"], list):
            problems.append("advice_patterns: リストで書いてください")
        for i, item in enumerate(_as_items(rules.get("advice_patterns"), "pattern")):
            regex(item.get("pattern"), f"advice_patterns[{i}]")
            severity(item, f"advice_patterns[{i}]")

    if "attributes" in rules:
        attrs = rules["attributes"]
        if not isinstance(attrs, dict):
            problems.append("attributes: {\"カテゴリ名\": {\"patterns\": [...]}} の形で書いてください")
        else:
            for cat, spec in attrs.items():
                if cat.startswith("_"):
                    continue
                s = _attr_spec(spec)
                where = f"attributes.{cat}"
                if not s:
                    problems.append(f"{where}: {{\"patterns\": [...]}} の形で書いてください")
                    continue
                pattern_list(s.get("patterns"), f"{where}.patterns")
                if "exclude" in s:
                    pattern_list(s.get("exclude"), f"{where}.exclude")
                severity(s, where)

    if "attribute_cluster_threshold" in rules:
        t = rules["attribute_cluster_threshold"]
        if isinstance(t, bool) or not isinstance(t, int) or t < 1:
            problems.append(f"attribute_cluster_threshold: 1以上の整数にしてください（{t!r}）")

    if "statistics" in rules:
        st = rules["statistics"]
        if not isinstance(st, dict):
            problems.append("statistics: オブジェクト（{...}）で書いてください")
        else:
            for key in ("number_patterns", "source_patterns", "year_patterns"):
                if key in st:
                    pattern_list(st[key], f"statistics.{key}")
                else:
                    problems.append(f"statistics.{key}: ありません")

    if "private_terms" in rules:
        if not isinstance(rules["private_terms"], list):
            problems.append("private_terms: リストで書いてください")
        for i, item in enumerate(_as_items(rules.get("private_terms"), "word")):
            where = f"private_terms[{i}]"
            if "pattern" in item:
                regex(item.get("pattern"), where)
            elif not isinstance(item.get("word"), str) or not item.get("word"):
                problems.append(f"{where}: 文字列、{{\"word\": ...}} または {{\"pattern\": ...}} で書いてください")

    if "ignore_patterns" in rules:
        pattern_list(rules["ignore_patterns"], "ignore_patterns")

    return problems


# ---------------------------------------------------------------- ルールのコンパイル（壊れた項目は飛ばす）

@dataclass
class _Rule:
    rx: "re.Pattern"
    rule: str
    note: str
    severity: str
    label: str = ""
    excepts: Tuple["re.Pattern", ...] = ()   # 禁句: この語の一部として出てきたら除外（例: 必ずしも）
    excludes: Tuple["re.Pattern", ...] = ()  # 属性: 一致した文字列がこれに当たれば除外


def _try_compile(pat) -> Optional["re.Pattern"]:
    if not isinstance(pat, str) or not pat:
        return None
    try:
        return re.compile(pat)
    except re.error:
        return None


def _compile_list(lst) -> List["re.Pattern"]:
    if not isinstance(lst, list):
        return []
    return [rx for rx in (_try_compile(p) for p in lst) if rx is not None]


def _sev(item: dict, default: str) -> str:
    s = item.get("severity", default)
    return s if s in SEVERITIES else default


def _note(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _compile(rules: dict) -> dict:
    c: dict = {}

    fw = []
    for i, item in enumerate(_as_items(rules.get("forbidden_words"), "word")):
        w = item.get("word")
        if not isinstance(w, str) or not w:
            continue
        exc = item.get("except", [])
        excepts = tuple(re.compile(re.escape(x)) for x in exc if isinstance(x, str) and x) if isinstance(exc, list) else ()
        fw.append(_Rule(re.compile(re.escape(w)), f"forbidden_words[{i}]:{w}", _note(item.get("note")),
                        _sev(item, "warn"), label=w, excepts=excepts))
    c["forbidden"] = fw

    adv = []
    for i, item in enumerate(_as_items(rules.get("advice_patterns"), "pattern")):
        rx = _try_compile(item.get("pattern"))
        if rx is not None:
            adv.append(_Rule(rx, f"advice_patterns[{i}]", _note(item.get("note")), _sev(item, "warn")))
    c["advice"] = adv

    attrs: Dict[str, List[_Rule]] = {}
    raw_attrs = rules.get("attributes")
    if isinstance(raw_attrs, dict):
        for cat, spec in raw_attrs.items():
            if cat.startswith("_"):
                continue
            s = _attr_spec(spec)
            excludes = tuple(_compile_list(s.get("exclude")))
            lst = []
            pats = s.get("patterns") if isinstance(s.get("patterns"), list) else []
            for i, p in enumerate(pats):
                rx = _try_compile(p)
                if rx is not None:
                    lst.append(_Rule(rx, f"attributes.{cat}[{i}]", _note(s.get("note")), _sev(s, "warn"),
                                     label=cat, excludes=excludes))
            if lst:
                attrs[cat] = lst
    c["attributes"] = attrs

    t = rules.get("attribute_cluster_threshold", DEFAULT_CLUSTER_THRESHOLD)
    c["cluster_threshold"] = t if isinstance(t, int) and not isinstance(t, bool) and t >= 1 else DEFAULT_CLUSTER_THRESHOLD
    c["cluster_note"] = _note(rules.get("attribute_cluster_note"))

    st = rules.get("statistics") if isinstance(rules.get("statistics"), dict) else {}
    c["stat_numbers"] = _compile_list(st.get("number_patterns"))
    c["stat_sources"] = _compile_list(st.get("source_patterns"))
    c["stat_years"] = _compile_list(st.get("year_patterns"))
    c["note_source"] = _note(st.get("note_source"))
    c["note_year"] = _note(st.get("note_year"))

    priv = []
    default_note = _note(rules.get("private_terms_note"))
    for i, item in enumerate(_as_items(rules.get("private_terms"), "word")):
        if "pattern" in item:
            rx = _try_compile(item.get("pattern"))
            label = str(item.get("pattern"))
        else:
            w = item.get("word")
            rx = re.compile(re.escape(w)) if isinstance(w, str) and w else None
            label = w
        if rx is not None:
            priv.append(_Rule(rx, f"private_terms[{i}]", _note(item.get("note")) or default_note, "strong", label=label))
    c["private"] = priv

    c["ignore"] = _compile_list(rules.get("ignore_patterns"))
    return c


# ---------------------------------------------------------------- 本文の下ごしらえ（元の本文は変えず、検査用の写しを作る）

_FENCE_RX = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEADING_RX = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
_FM_LINE_RX = re.compile(r"^\s*[^:\s][^:]*:")
_FM_KEEP_RX = re.compile(r"^\s*(?:title|タイトル)\s*:")
_URL_RX = re.compile(r"https?://[^\s)）\]」>]+|www\.[^\s)）\]」>]+")
_SOURCE_LINE_RX = re.compile(r"^[\s（(※*＊>\-・]*(?:出典|出所|参考|引用|ソース|[Ss]ource|データ)")


@dataclass
class _Para:
    start_line: int      # 1始まり
    text: str            # 検査用の写し（行を \n で連結）
    offsets: List[int]   # 各行の先頭位置

    def pos(self, off: int) -> Tuple[int, int]:
        i = bisect.bisect_right(self.offsets, off) - 1
        return self.start_line + i, off - self.offsets[i]

    def line_end(self, off: int) -> int:
        i = bisect.bisect_right(self.offsets, off) - 1
        nxt = self.offsets[i + 1] - 1 if i + 1 < len(self.offsets) else len(self.text)
        return nxt - self.offsets[i]


@dataclass
class _Doc:
    name: str
    raw_lines: List[str]
    lines: List[str]     # 検査用の写し（コードブロック・front matter・除外記法を伏せたもの。長さは元と同じ）
    paras: List[_Para]


def _blank_same_length(s: str) -> str:
    return re.sub(r"[^\n]", " ", s)


def _prepare(text: str, c: dict, name: str) -> _Doc:
    raw_lines = text.split("\n")
    lines = [ln.replace("\r", " ") for ln in raw_lines]
    blank = [False] * len(lines)

    # front matter（先頭の --- から --- まで。「キー: 値」の行だけのときに限る）。title 行だけは検査する
    if lines and lines[0].strip() == "---":
        for j in range(1, min(len(lines), 60)):
            if lines[j].strip() == "---":
                inner = lines[1:j]
                if all(not x.strip() or _FM_LINE_RX.match(x) for x in inner):
                    for i in range(0, j + 1):
                        if not _FM_KEEP_RX.match(lines[i]):
                            lines[i] = " " * len(lines[i])
                            blank[i] = True
                break
            if not (not lines[j].strip() or _FM_LINE_RX.match(lines[j])):
                break

    # コードブロック（``` / ~~~）は検査しない
    fence = None
    for i, ln in enumerate(lines):
        m = _FENCE_RX.match(ln)
        if fence is None:
            if m:
                fence = m.group(1)[0]
                lines[i] = " " * len(ln)
                blank[i] = True
        else:
            lines[i] = " " * len(ln)
            blank[i] = True
            if m and m.group(1)[0] == fence:
                fence = None

    for i, ln in enumerate(lines):
        if not ln.strip():
            blank[i] = True

    # 除外記法（[要追加：…] 等）は同じ長さの空白に置き換える（行・位置がずれない）
    joined = "\n".join(lines)
    for rx in c["ignore"]:
        joined = rx.sub(lambda m: _blank_same_length(m.group(0)), joined)
    lines = joined.split("\n")

    # 段落: 空行区切り。見出し行はそれだけで1段落。除外記法だけの行は段落を切らない
    paras: List[_Para] = []
    cur: List[Tuple[int, str]] = []

    def flush() -> None:
        if cur:
            offs, pos = [], 0
            for _, t in cur:
                offs.append(pos)
                pos += len(t) + 1
            paras.append(_Para(cur[0][0], "\n".join(t for _, t in cur), offs))
            cur.clear()

    for i, ln in enumerate(lines):
        if blank[i]:
            flush()
        elif _HEADING_RX.match(ln):
            flush()
            cur.append((i + 1, ln))
            flush()
        else:
            cur.append((i + 1, ln))
    flush()
    return _Doc(name, raw_lines, lines, paras)


# ---------------------------------------------------------------- Finding の組み立て

def _excerpt(line_text: str, start: int = -1, end: int = -1, width: int = 80) -> str:
    t = line_text.rstrip("\r")
    if len(t.strip()) <= width:
        return t.strip()
    if start < 0:
        return t[:width - 1].strip() + "…"
    span = max(end - start, 1)
    ctx = max((width - span) // 2, 10)
    a = max(0, start - ctx)
    b = min(len(t), max(end, start + 1) + ctx)
    if b - a > width:
        b = a + width
    out = t[a:b]
    return ("…" if a > 0 else "") + out + ("…" if b < len(t) else "")


def _finding(doc: _Doc, category: str, severity: str, line: int, start: int, end: int,
             message: str, rule: str, match: Optional[str] = None) -> Finding:
    raw = doc.raw_lines[line - 1] if 0 < line <= len(doc.raw_lines) else ""
    if match is None:
        match = raw[start:end] if start >= 0 else ""
    return Finding(category=category, severity=severity, doc=doc.name, line=line, match=match,
                   excerpt=_excerpt(raw, start, end), message=message, rule=rule, start=start, end=end)


def _para_finding(doc: _Doc, p: _Para, s: int, e: int, category: str, severity: str,
                  message: str, rule: str) -> Finding:
    line, col = p.pos(s)
    col_end = min(col + (e - s), p.line_end(s))  # 行をまたぐ一致は行末まで
    return _finding(doc, category, severity, line, col, col_end, message, rule)


def _inside(s: int, e: int, spans: Sequence[Tuple[int, int]]) -> bool:
    return any(a <= s and e <= b for a, b in spans)


def _overlaps(s: int, e: int, spans: Sequence[Tuple[int, int]]) -> bool:
    return any(s < b and a < e for a, b in spans)


def _dedupe(items: List[tuple]) -> List[tuple]:
    """(start, end, ...) の重なりを除く。長い一致を優先し、位置順で返す。"""
    kept: List[tuple] = []
    for it in sorted(items, key=lambda x: (-(x[1] - x[0]), x[0])):
        if not _overlaps(it[0], it[1], [(k[0], k[1]) for k in kept]):
            kept.append(it)
    return sorted(kept, key=lambda x: x[0])


def _scan(rule: _Rule, text: str, blocked: Sequence[Tuple[int, int]]):
    for m in rule.rx.finditer(text):
        s, e = m.start(), m.end()
        if s == e or _inside(s, e, blocked):
            continue
        yield s, e, m.group(0)


# ---------------------------------------------------------------- 各チェック

def _check_private(doc: _Doc, c: dict) -> List[Finding]:
    """伏せ字は漏れ防止なので、コードブロック・除外記法・front matter の中も含めて元の本文全体を見る。"""
    out = []
    for n, raw in enumerate(doc.raw_lines, 1):
        for r in c["private"]:
            for m in r.rx.finditer(raw):
                if m.start() == m.end():
                    continue
                msg = f"伏せたい語「{m.group(0)}」がそのまま書かれています。{r.note}".strip()
                out.append(_finding(doc, "伏せ字", "strong", n, m.start(), m.end(), msg, r.rule))
    return out


def _check_forbidden(doc: _Doc, p: _Para, c: dict, urls) -> List[Finding]:
    hits = []
    for r in c["forbidden"]:
        exc_spans = [(m.start(), m.end()) for x in r.excepts for m in x.finditer(p.text)]
        for s, e, _ in _scan(r, p.text, urls):
            if not _inside(s, e, exc_spans):
                hits.append((s, e, r))
    out = []
    for s, e, r in _dedupe(hits):
        msg = f"禁句「{r.label}」があります。{r.note}".strip()
        out.append(_para_finding(doc, p, s, e, "禁句", r.severity, msg, r.rule))
    return out


def _check_advice(doc: _Doc, p: _Para, c: dict, urls) -> List[Finding]:
    hits = [(s, e, r, g) for r in c["advice"] for s, e, g in _scan(r, p.text, urls)]
    out = []
    for s, e, r, g in _dedupe(hits):
        msg = f"個別の助言に読める言い回し（「{g}」）です。{r.note}".strip()
        out.append(_para_finding(doc, p, s, e, "助言表現", r.severity, msg, r.rule))
    return out


def _check_attributes(doc: _Doc, p: _Para, c: dict, urls) -> List[Finding]:
    out: List[Finding] = []
    found: Dict[str, List[str]] = {}
    for cat, rules in c["attributes"].items():
        hits = []
        for r in rules:
            for s, e, g in _scan(r, p.text, urls):
                if any(x.search(g) for x in r.excludes):
                    continue
                hits.append((s, e, r, g))
        for s, e, r, g in _dedupe(hits):
            found.setdefault(cat, []).append(g)
            msg = f"ぼかすべき属性（{cat}）の具体的な記述かもしれません:「{g}」。{r.note}".strip()
            out.append(_para_finding(doc, p, s, e, "属性", r.severity, msg, r.rule))
    if len(found) >= c["cluster_threshold"]:
        detail = "、".join(f"{cat}（{'・'.join(dict.fromkeys(v))}）" for cat, v in found.items())
        msg = (f"同じ段落に {len(found)} 種類の属性が集まっています: {detail}。"
               f"{c['cluster_note']}").strip()
        out.append(_finding(doc, "属性の集中", "warn", p.start_line, -1, -1, msg,
                            "attribute_cluster_threshold", match="・".join(found)))
    return out


def _check_statistics(doc: _Doc, p: _Para, nxt: Optional[_Para], c: dict, urls) -> List[Finding]:
    hits = []
    for rx in c["stat_numbers"]:
        for m in rx.finditer(p.text):
            if m.start() != m.end() and not _inside(m.start(), m.end(), urls):
                hits.append((m.start(), m.end(), m.group(0)))
    hits = _dedupe(hits)
    if not hits:
        return []
    # 出典・年度は、直後の段落が「出典：…」「※参考…」で始まる場合はそこも見る
    scope = p.text
    if nxt is not None and _SOURCE_LINE_RX.match(nxt.text.strip()):
        scope = scope + "\n" + nxt.text
    s, e, first = hits[0]
    more = f"ほか{len(hits) - 1}件" if len(hits) > 1 else ""
    out = []
    if not any(rx.search(scope) for rx in c["stat_sources"]):
        msg = f"統計らしい数値（「{first}」{more}）がありますが、出典が見当たりません。{c['note_source']}".strip()
        out.append(_para_finding(doc, p, s, e, "出典", "warn", msg, "statistics.source_patterns"))
    if not any(rx.search(scope) for rx in c["stat_years"]):
        msg = f"統計らしい数値（「{first}」{more}）がありますが、調査年・年度が見当たりません。{c['note_year']}".strip()
        out.append(_para_finding(doc, p, s, e, "年度", "warn", msg, "statistics.year_patterns"))
    return out


# 曜日（ルールファイルではなく固定。暦の計算なので）
_WEEKDAYS = "月火水木金土日"
_ERA_BASE = {"令和": 2018, "平成": 1988, "昭和": 1925, "大正": 1911}
_DATE_WD_RX = re.compile(
    r"(?:(?P<era>令和|平成|昭和|大正)\s*(?P<ey>元|\d{1,2})|(?<!\d)(?P<y>\d{4}))\s*"
    r"(?:年\s*(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日|[/／.\-]\s*(?P<m2>\d{1,2})\s*[/／.\-]\s*(?P<d2>\d{1,2}))"
    r"\s*[（(]\s*(?P<w>[月火水木金土日])\s*(?:曜日|曜)?\s*[)）]"
)


def _era_year(era: str, ey: str) -> int:
    return _ERA_BASE[era] + (1 if ey == "元" else int(ey))


def _check_weekday(doc: _Doc, p: _Para, urls) -> List[Finding]:
    out = []
    for m in _DATE_WD_RX.finditer(p.text):
        if _inside(m.start(), m.end(), urls):
            continue
        y = _era_year(m.group("era"), m.group("ey")) if m.group("era") else int(m.group("y"))
        mo = int(m.group("m") or m.group("m2"))
        d = int(m.group("d") or m.group("d2"))
        w = m.group("w")
        try:
            actual = _WEEKDAYS[_dt.date(y, mo, d).weekday()]
        except ValueError:
            msg = f"{y}年{mo}月{d}日 は存在しない日付です（曜日を確認できません）。"
            out.append(_para_finding(doc, p, m.start(), m.end(), "曜日", "warn", msg, "weekday"))
            continue
        if actual != w:
            msg = (f"{y}年{mo}月{d}日 は{actual}曜日ですが、本文では（{w}）になっています。"
                   "日付か曜日のどちらかが誤っている可能性があります。")
            out.append(_para_finding(doc, p, m.start(), m.end(), "曜日", "warn", msg, "weekday"))
    return out


def _sort_key(f: Finding) -> Tuple[int, int, int]:
    return (f.line, f.start, CATEGORIES.index(f.category) if f.category in CATEGORIES else len(CATEGORIES))


def _check_doc(doc: _Doc, c: dict, weekday_only: bool = False) -> List[Finding]:
    out: List[Finding] = [] if weekday_only else _check_private(doc, c)
    for i, p in enumerate(doc.paras):
        urls = [(m.start(), m.end()) for m in _URL_RX.finditer(p.text)]
        if not weekday_only:
            out += _check_forbidden(doc, p, c, urls)
            out += _check_advice(doc, p, c, urls)
            out += _check_attributes(doc, p, c, urls)
            nxt = doc.paras[i + 1] if i + 1 < len(doc.paras) else None
            out += _check_statistics(doc, p, nxt, c, urls)
        out += _check_weekday(doc, p, urls)
    out.sort(key=_sort_key)
    return out


# ---------------------------------------------------------------- 公開API

def check_text(text: str, rules: Optional[dict] = None, name: str = "") -> List[Finding]:
    """1つの下書きを検査して警告の一覧を返す（行番号順）。本文は変更しない。"""
    if rules is None:
        rules = load_rules()
    c = _compile(rules)
    return _check_doc(_prepare(text, c, name), c)


# シリーズ矛盾（生年）
_SENT_RX = re.compile(r"[^。！？!?]+")
_YEAR_RX = re.compile(
    r"(?:(?P<era>令和|平成|昭和|大正)\s*(?P<ey>元|\d{1,2})|(?<!\d)(?P<y>[12１２]\d{3}))\s*年"
    r"(?!代|間|前|後|ぶり|分|以上|以内|目)(?P<born>\s*(?:生まれ|生))?"
)
_AGE_RX = re.compile(
    r"(?<!\d)(?P<a>\d{1,3})\s*(?:歳|才)"
    r"(?!\s*(?:年上|年下|年の差|上|下|差|違い|以上|以下|未満|前後))"
)
# 年齢の直後が「の＋他人」なら本人の年齢ではないとみなす（例: 30歳の男性、5歳の娘）
_OTHER_AFTER_RX = re.compile(
    r"\s*の\s*(?:娘|息子|子|長男|長女|次男|次女|妻|夫|父|母|兄|姉|弟|妹|孫|男性|女性|男|女|人|方|彼|彼女|友人|相手|少年|少女)"
)
_SELF_WORDS = r"私|僕|俺|自分|わたし|ぼく|おれ|筆者"
_OTHER_WORDS = (r"妻|夫|娘|息子|長男|長女|次男|次女|父|母|兄|姉|弟|妹|祖父|祖母|孫|彼女|彼|相手|友人|上司|同僚|"
                r"被害者|子ども|子供")
_SUBJECT_RX = re.compile(rf"(?P<self>{_SELF_WORDS})|(?P<other>{_OTHER_WORDS})")


def _age_is_other(sentence: str, a_start: int, a_end: int) -> bool:
    if _OTHER_AFTER_RX.match(sentence, a_end):
        return True
    last = None
    for m in _SUBJECT_RX.finditer(sentence, max(0, a_start - 12), a_start):
        last = m
    return bool(last and last.group("other"))


def _birth_entries(doc: _Doc) -> List[dict]:
    """各行の各文から (生年, 根拠) を集める。同じ文に「年」と「N歳」があれば 生年≈年−N。「YYYY年生まれ」はそのまま。"""
    entries = []
    for n, ln in enumerate(doc.lines, 1):
        for sm in _SENT_RX.finditer(ln):
            sent, base = sm.group(0), sm.start()
            years = []
            for ym in _YEAR_RX.finditer(sent):
                y = _era_year(ym.group("era"), ym.group("ey")) if ym.group("era") else int(ym.group("y"))
                if ym.group("born"):
                    entries.append({"birth": y, "line": n, "start": base + ym.start(), "end": base + ym.end(),
                                    "how": f"{y}年生まれ"})
                else:
                    years.append((ym.start(), ym.end(), y))
            if not years:
                continue
            for am in _AGE_RX.finditer(sent):
                if _age_is_other(sent, am.start(), am.end()):
                    continue
                age = int(am.group("a"))
                ys, ye, y = min(years, key=lambda t: min(abs(t[0] - am.end()), abs(am.start() - t[1])))
                s, e = min(ys, am.start()), max(ye, am.end())
                entries.append({"birth": y - age, "line": n, "start": base + s, "end": base + e,
                                "how": f"{y}年に{age}歳"})
    return entries


def check_series(docs: List[Tuple[str, str]], rules: Optional[dict] = None) -> List[Finding]:
    """シリーズ（前編→後編の並び順）内の矛盾を検出する。

    - 生年の矛盾: 同じ文の「年」と「N歳」から逆算した生年の幅が、全記事を通して2以上なら、該当箇所すべてに警告。
    - 曜日の矛盾: 「YYYY年M月D日（曜）」の曜日が暦と違えば警告（check_text と同じ内容を各記事について出す）。
    並びは記事の順 → 行番号順。本文は変更しない。
    """
    if rules is None:
        rules = load_rules()
    c = _compile(rules)
    prepared = [_prepare(text, c, name) for name, text in docs]
    per_doc: List[List[Finding]] = [_check_doc(d, c, weekday_only=True) for d in prepared]

    all_entries = [(i, d, e) for i, d in enumerate(prepared) for e in _birth_entries(d)]
    births = [e["birth"] for _, _, e in all_entries]
    if births and max(births) - min(births) >= 2:
        listing = " / ".join(
            f"{d.name or '（無題）'} {e['line']}行目: {e['how']}→生年≈{e['birth']}年" for _, d, e in all_entries)
        for i, d, e in all_entries:
            msg = (f"年齢と年から逆算した生年が記事の間で食い違っています（この箇所: {e['how']} → 生年≈{e['birth']}年、"
                   f"全体の幅 {min(births)}〜{max(births)}年）。該当箇所: {listing}。"
                   "他の人の年齢を拾っている場合もあるので、人が確認してください。")
            per_doc[i].append(_finding(d, "シリーズ矛盾", "warn", e["line"], e["start"], e["end"], msg,
                                       "series.birth_year"))
    out: List[Finding] = []
    for lst in per_doc:
        out += sorted(lst, key=_sort_key)
    return out


def summarize(findings: Sequence[Finding]) -> dict:
    """{"total": 件数, "by_category": {カテゴリ: 件数}, "by_severity": {重さ: 件数}}（件数0のカテゴリは含めない）。"""
    by_cat: Dict[str, int] = {}
    by_sev: Dict[str, int] = {}
    for f in findings:
        by_cat[f.category] = by_cat.get(f.category, 0) + 1
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    order = {c: i for i, c in enumerate(CATEGORIES)}
    by_cat = dict(sorted(by_cat.items(), key=lambda kv: order.get(kv[0], len(order))))
    by_sev = {s: by_sev[s] for s in SEVERITIES if s in by_sev}
    return {"total": len(findings), "by_category": by_cat, "by_severity": by_sev}
