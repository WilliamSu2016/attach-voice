"""edge-tts 实现 TTSProvider（唯一允许直接 import edge_tts 的模块，规则 B10）。

职责（ARCHITECTURE.md#5）：
- 音色列表缓存、按语言过滤
- 长文本按句子分块合成后拼接（PRODUCT.md「长文本必须按句子分块合成」）
- 网络失败自动重试（指数退避），最终失败抛出 TTSNetworkError
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Callable, List, Optional

import edge_tts
from mutagen.mp3 import MP3

from .tts_provider import TTSNetworkError, TTSProvider, Voice

# 单块最大字符数：避免单次 TTS 请求文本过长导致超时/被截断
DEFAULT_MAX_CHUNK_CHARS = 300
# 网络失败重试次数（含首次尝试），满足 PRODUCT.md「至少 3 次」
DEFAULT_MAX_RETRIES = 3
# 指数退避基数（秒）：第 n 次重试等待 backoff_base * 2**(n-1)
DEFAULT_BACKOFF_BASE = 1.0

# 中英文句子终止符：句号/问号/叹号（含全角）
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?.])\s*")

# 网络/引擎层面的可重试异常；其余异常（如参数错误）直接向上抛出，不重试
_RETRYABLE_EXCEPTIONS = (edge_tts.exceptions.EdgeTTSException, OSError, TimeoutError)


def split_into_chunks(text: str, max_chars: int = DEFAULT_MAX_CHUNK_CHARS) -> List[str]:
    """按句子分块，单块不超过 max_chars；单句超长则按 max_chars 硬切分。"""
    text = text.strip()
    if not text:
        return []

    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    if not sentences:
        sentences = [text]

    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(sentence), max_chars):
                chunks.append(sentence[i : i + max_chars])
            continue

        if current and len(current) + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current += sentence

    if current:
        chunks.append(current)

    return chunks


class EdgeTTSProvider(TTSProvider):
    """edge-tts 实现。构造参数均有默认值，测试时可注入更小的重试/退避以加速用例。"""

    def __init__(
        self,
        max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        sleep_fn: Optional[Callable[[float], None]] = None,
    ) -> None:
        self._max_chunk_chars = max_chunk_chars
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep_fn or time.sleep
        self._voices_cache: Optional[List[Voice]] = None

    def list_voices(self, language: Optional[str] = None) -> List[Voice]:
        voices = self._fetch_voices()
        if language is None:
            return list(voices)
        lang_lower = language.lower()
        return [v for v in voices if lang_lower in v.locale.lower()]

    def _fetch_voices(self) -> List[Voice]:
        if self._voices_cache is None:
            raw_voices = asyncio.run(edge_tts.list_voices())
            self._voices_cache = [
                Voice(
                    short_name=v["ShortName"],
                    locale=v["Locale"],
                    gender=v["Gender"],
                    friendly_name=v.get("FriendlyName", ""),
                )
                for v in raw_voices
            ]
        return self._voices_cache

    def synthesize(
        self, text: str, voice: str, rate: str, pitch: str, out_path: str
    ) -> float:
        chunks = split_into_chunks(text, self._max_chunk_chars)
        if not chunks:
            raise ValueError("text 不能为空")

        out_path_obj = Path(out_path)
        out_path_obj.parent.mkdir(parents=True, exist_ok=True)

        if len(chunks) == 1:
            try:
                self._synthesize_chunk_with_retry(
                    chunks[0], voice, rate, pitch, str(out_path_obj)
                )
            except Exception:
                out_path_obj.unlink(missing_ok=True)
                raise
        else:
            chunk_paths: List[Path] = []
            try:
                for i, chunk in enumerate(chunks):
                    chunk_path = out_path_obj.with_name(
                        f"{out_path_obj.stem}.part{i}{out_path_obj.suffix}"
                    )
                    self._synthesize_chunk_with_retry(
                        chunk, voice, rate, pitch, str(chunk_path)
                    )
                    chunk_paths.append(chunk_path)
                self._concatenate(chunk_paths, out_path_obj)
            finally:
                for p in chunk_paths:
                    p.unlink(missing_ok=True)

        return self._measure_duration(out_path_obj)

    def _synthesize_chunk_with_retry(
        self, text: str, voice: str, rate: str, pitch: str, out_path: str
    ) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(self._max_retries):
            try:
                asyncio.run(self._synthesize_once(text, voice, rate, pitch, out_path))
                return
            except _RETRYABLE_EXCEPTIONS as exc:
                last_error = exc
                if attempt < self._max_retries - 1:
                    self._sleep(self._backoff_base * (2**attempt))
        raise TTSNetworkError(
            f"TTS 合成失败，已重试 {self._max_retries} 次仍未成功：{last_error}"
        ) from last_error

    @staticmethod
    async def _synthesize_once(
        text: str, voice: str, rate: str, pitch: str, out_path: str
    ) -> None:
        communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
        await communicate.save(out_path)

    @staticmethod
    def _concatenate(chunk_paths: List[Path], out_path: Path) -> None:
        """按顺序二进制拼接各段 mp3。CBR 编码下拼接后仍可被播放器正常解码。"""
        with open(out_path, "wb") as out_f:
            for chunk_path in chunk_paths:
                with open(chunk_path, "rb") as in_f:
                    out_f.write(in_f.read())

    @staticmethod
    def _measure_duration(path: Path) -> float:
        return float(MP3(str(path)).info.length)
