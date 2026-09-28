"""Perplexity API（Sonar 系・Deep Research）の呼び出しと料金計算。

料金・仕様は要確認（https://docs.perplexity.ai/docs/getting-started/pricing）。単価は config/research.json。
- 実コストはレスポンスの usage.cost.total_cost があればそれ（cost_source="api"）、
  なければ設定の単価で計算する（cost_source="computed"）。
- エラーメッセージは日本語にし、必ず secrets.redact を通す。HTTP の本文・ヘッダはそのまま出さない。
- transport を差し替えられる（テストでは偽物を渡し、本物の API は呼ばない）。
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import secrets

Transport = Callable[[str, Dict[str, str], bytes, float], Tuple[int, bytes]]
DEFAULT_ENDPOINT = "https://api.perplexity.ai/chat/completions"


class PplxError(RuntimeError):
    """Perplexity の呼び出しに失敗した。status: HTTP ステータス（通信エラーは 0）。
    retryable: 時間をおけば通る見込みがあるか。maybe_charged: 課金が発生した可能性があるか。"""

    def __init__(self, message: str, status: int = 0, retryable: bool = False, maybe_charged: bool = False):
        super().__init__(secrets.redact(message))
        self.status = status
        self.retryable = retryable
        self.maybe_charged = maybe_charged


@dataclass
class PplxResult:
    text: str
    sources: List[dict] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    cost_source: str = "computed"
    model: str = ""


class _Timeout(Exception):
    pass


def urllib_transport(url: str, headers: Dict[str, str], body: bytes, timeout: float) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        try:
            data = e.read()
        except Exception:
            data = b""
        return e.code, data
    except (socket.timeout, TimeoutError) as e:
        raise _Timeout() from e
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            raise _Timeout() from e
        raise


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def compute_cost(usage: dict, pricing: dict, search_context_size: str = "") -> float:
    """usage（Perplexity の usage 形式）と単価表から USD を計算する。"""
    usage = usage or {}
    pricing = pricing or {}
    cost = (_f(usage.get("prompt_tokens")) * _f(pricing.get("input_per_m"))
            + _f(usage.get("completion_tokens")) * _f(pricing.get("output_per_m"))
            + _f(usage.get("citation_tokens")) * _f(pricing.get("citation_per_m"))
            + _f(usage.get("reasoning_tokens")) * _f(pricing.get("reasoning_per_m"))) / 1_000_000
    cost += _f(usage.get("num_search_queries")) * _f(pricing.get("search_query_per_k")) / 1000
    req = pricing.get("request_per_k") or {}
    if isinstance(req, dict):
        size = search_context_size or "medium"
        cost += _f(req.get(size, req.get("medium"))) / 1000
    else:
        cost += _f(req) / 1000
    return round(cost, 6)


_EST_KEYS = {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens",
             "citation_tokens": "citation_tokens", "reasoning_tokens": "reasoning_tokens",
             "search_queries": "num_search_queries"}


def estimate_cost(provider_cfg: dict, kind: str) -> Tuple[float, float]:
    """設定の想定トークン数・検索回数（[少ない, 多い]）から (usd_low, usd_high)。"""
    est = ((provider_cfg or {}).get("estimate") or {}).get(kind) or {}
    low: Dict[str, float] = {}
    high: Dict[str, float] = {}
    for k, uk in _EST_KEYS.items():
        v = est.get(k)
        if isinstance(v, (list, tuple)) and v:
            low[uk], high[uk] = _f(v[0]), _f(v[-1])
        elif v is not None:
            low[uk] = high[uk] = _f(v)
    pricing = provider_cfg.get("pricing") or {}
    size = provider_cfg.get("search_context_size") or ""
    return compute_cost(low, pricing, size), compute_cost(high, pricing, size)


def _error_message(status: int, body: bytes) -> str:
    detail = ""
    try:
        data = json.loads(body.decode("utf-8", "replace"))
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            detail = str(err.get("message") or "")
        elif isinstance(err, str):
            detail = err
    except (ValueError, AttributeError):
        detail = ""
    detail = re.sub(r"\s+", " ", detail).strip()[:200]
    if status == 401:
        return "APIキーが無効か、Perplexity の残高がなくなっている可能性があります（401）。キーと残高を確認してください。"
    if status == 403:
        return "Perplexity に拒否されました（403）。APIキーの権限やアカウントの状態を確認してください。"
    if status == 429:
        return "Perplexity が混み合っています（429）。少し待ってからもう一度試してください。※429は課金されません。"
    if status in (400, 404, 422):
        msg = f"Perplexity がリクエストを受け付けませんでした（{status}）。モデル名や設定を確認してください。"
        return msg + (f"（Perplexity の説明: {detail}）" if detail else "")
    if status >= 500:
        return (f"Perplexity 側の一時的な障害です（{status}）。時間をおいて試してください。"
                "※途中まで処理されて課金されている可能性があります。")
    return f"Perplexity の呼び出しに失敗しました（{status}）。"


def _sources(data: dict) -> List[dict]:
    out: List[dict] = []
    seen = set()
    for r in data.get("search_results") or []:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({"title": str(r.get("title") or "").strip(), "url": url,
                    "date": str(r.get("date") or r.get("last_updated") or "").strip()})
    for u in data.get("citations") or []:  # 旧形式（廃止済み）が来た場合の保険
        u = str(u or "").strip()
        if u and u not in seen:
            seen.add(u)
            out.append({"title": "", "url": u, "date": ""})
    return out


_THINK_RX = re.compile(r"<think>.*?</think>\s*", re.S)


def call(prompt: str, provider_cfg: dict, api_key: str, *, system: str = "",
         transport: Optional[Transport] = None) -> PplxResult:
    if not api_key:
        raise PplxError("Perplexity の APIキーが設定されていません。リサーチ画面の設定から登録してください。")
    transport = transport or urllib_transport
    model = str(provider_cfg.get("model") or "")
    url = str(provider_cfg.get("endpoint") or DEFAULT_ENDPOINT)
    timeout = _f(provider_cfg.get("timeout_sec")) or 180.0
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload: dict = {"model": model, "messages": messages}
    size = provider_cfg.get("search_context_size") or ""
    if size:
        payload["web_search_options"] = {"search_context_size": size}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
               "Accept": "application/json"}
    try:
        status, raw = transport(url, headers, body, timeout)
    except _Timeout:
        raise PplxError(f"Perplexity の応答が {int(timeout)} 秒以内に返りませんでした。"
                        "※処理が進んで課金されている可能性があります。", 0, True, True) from None
    except urllib.error.URLError as e:
        raise PplxError("Perplexity に接続できませんでした（ネットワークを確認してください）: "
                        + str(getattr(e, "reason", "")), 0, True, False) from None
    except (OSError, ValueError) as e:
        raise PplxError("Perplexity との通信中にエラーが起きました: " + type(e).__name__, 0, True, True) from None
    if status != 200:
        raise PplxError(_error_message(status, raw or b""), status,
                        retryable=status == 429 or status >= 500, maybe_charged=status >= 500)
    try:
        data = json.loads((raw or b"").decode("utf-8", "replace"))
        text = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise PplxError("Perplexity の応答を読み取れませんでした（形式が想定と違います）。"
                        "※課金されている可能性があります。", status, False, True) from None
    text = _THINK_RX.sub("", str(text or "")).strip()
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    cost_info = usage.get("cost") if isinstance(usage.get("cost"), dict) else {}
    total = cost_info.get("total_cost")
    if isinstance(total, (int, float)) and not isinstance(total, bool):
        cost, source = float(total), "api"
    else:
        cost, source = compute_cost(usage, provider_cfg.get("pricing") or {}, size), "computed"
    return PplxResult(text=text, sources=_sources(data), usage=usage, cost_usd=cost, cost_source=source,
                      model=str(data.get("model") or model))
