"""tests/test_tts_provider.py — TTSProvider / EdgeTTSProvider 的纯单测。

不依赖真实网络：edge_tts.list_voices 与 EdgeTTSProvider._synthesize_once 均通过
monkeypatch 隔离；mutagen.MP3 的时长读取也通过 monkeypatch 隔离，避免依赖真实音频文件格式。
真实联网校验（audience: F03 verification 第 3、4 条）在本文件之外，通过手动执行
`scripts/verify_tts_duration.py` 与直接调用 list_voices('zh') 完成。
"""
from __future__ import annotations

import inspect

import pytest

from src.core import edge_tts_provider as etp
from src.core.tts_provider import TTSNetworkError, TTSProvider, Voice


# ---------------------------------------------------------------------------
# ABC 契约
# ---------------------------------------------------------------------------


def test_tts_provider_is_abstract():
    assert inspect.isabstract(TTSProvider)
    with pytest.raises(TypeError):
        TTSProvider()  # type: ignore[abstract]


def test_edge_tts_provider_is_concrete_subclass():
    assert issubclass(etp.EdgeTTSProvider, TTSProvider)
    provider = etp.EdgeTTSProvider()
    assert isinstance(provider, TTSProvider)


def test_voice_dataclass_fields():
    v = Voice(short_name="zh-CN-XiaoxiaoNeural", locale="zh-CN", gender="Female")
    assert v.short_name == "zh-CN-XiaoxiaoNeural"
    assert v.locale == "zh-CN"
    assert v.gender == "Female"
    assert v.friendly_name == ""


# ---------------------------------------------------------------------------
# split_into_chunks
# ---------------------------------------------------------------------------


def test_split_into_chunks_empty_text():
    assert etp.split_into_chunks("") == []
    assert etp.split_into_chunks("   ") == []


def test_split_into_chunks_short_text_single_chunk():
    chunks = etp.split_into_chunks("你好，世界。", max_chars=300)
    assert chunks == ["你好，世界。"]


def test_split_into_chunks_respects_sentence_boundaries():
    text = "第一句。" + "第二句。" + "第三句。"
    chunks = etp.split_into_chunks(text, max_chars=10)
    # 每句 4 个字符（含句号），10 字符阈值下应拆成多块，且每块都在句号处断开
    assert "".join(chunks) == text
    for chunk in chunks:
        assert len(chunk) <= 10 or chunk in ("第一句。", "第二句。", "第三句。")


def test_split_into_chunks_hard_splits_oversized_single_sentence():
    long_sentence = "a" * 50  # 无标点的超长单句
    chunks = etp.split_into_chunks(long_sentence, max_chars=20)
    assert len(chunks) == 3
    assert "".join(chunks) == long_sentence
    assert all(len(c) <= 20 for c in chunks)


def test_split_into_chunks_never_exceeds_max_chars_for_normal_sentences():
    text = "。".join(["句子" + str(i) for i in range(20)]) + "。"
    chunks = etp.split_into_chunks(text, max_chars=15)
    assert all(len(c) <= 15 for c in chunks)
    assert "".join(chunks) == text


# ---------------------------------------------------------------------------
# list_voices（mock edge_tts.list_voices，隔离网络）
# ---------------------------------------------------------------------------


def test_list_voices_filters_by_language(monkeypatch):
    raw_voices = [
        {"ShortName": "zh-CN-XiaoxiaoNeural", "Locale": "zh-CN", "Gender": "Female", "FriendlyName": "Xiaoxiao"},
        {"ShortName": "en-US-JennyNeural", "Locale": "en-US", "Gender": "Female", "FriendlyName": "Jenny"},
        {"ShortName": "zh-TW-HsiaoChenNeural", "Locale": "zh-TW", "Gender": "Female", "FriendlyName": "HsiaoChen"},
    ]
    call_count = {"n": 0}

    async def fake_list_voices():
        call_count["n"] += 1
        return raw_voices

    monkeypatch.setattr(etp.edge_tts, "list_voices", fake_list_voices)

    provider = etp.EdgeTTSProvider()
    zh_voices = provider.list_voices("zh")
    assert len(zh_voices) == 2
    assert all("zh" in v.locale.lower() for v in zh_voices)

    all_voices = provider.list_voices(None)
    assert len(all_voices) == 3

    # 第二次调用应命中缓存，list_voices 只被真正获取一次
    assert call_count["n"] == 1


def test_list_voices_no_language_returns_all(monkeypatch):
    raw_voices = [
        {"ShortName": "en-US-JennyNeural", "Locale": "en-US", "Gender": "Female", "FriendlyName": "Jenny"},
    ]
    async def fake_list_voices():
        return raw_voices

    monkeypatch.setattr(etp.edge_tts, "list_voices", fake_list_voices)
    provider = etp.EdgeTTSProvider()
    voices = provider.list_voices()
    assert len(voices) == 1
    assert voices[0].short_name == "en-US-JennyNeural"


# ---------------------------------------------------------------------------
# synthesize（mock _synthesize_once 与 MP3，隔离网络与真实音频解析）
# ---------------------------------------------------------------------------


def _install_fake_synthesize(monkeypatch, fail_times=0, exception_cls=None):
    """安装一个假的 _synthesize_once：前 fail_times 次抛异常，之后成功写入占位文件。"""
    state = {"calls": 0}
    exception_cls = exception_cls or OSError

    async def fake_synthesize_once(text, voice, rate, pitch, out_path):
        state["calls"] += 1
        if state["calls"] <= fail_times:
            raise exception_cls("simulated network failure")
        with open(out_path, "wb") as f:
            f.write(b"fake-mp3-bytes")

    monkeypatch.setattr(etp.EdgeTTSProvider, "_synthesize_once", staticmethod(fake_synthesize_once))
    monkeypatch.setattr(etp, "MP3", lambda path: type("FakeInfo", (), {"info": type("I", (), {"length": 1.23})()})())
    return state


def test_synthesize_single_chunk_success(monkeypatch, tmp_path):
    _install_fake_synthesize(monkeypatch, fail_times=0)
    provider = etp.EdgeTTSProvider(sleep_fn=lambda s: None)
    out_path = tmp_path / "out.mp3"

    duration = provider.synthesize("短文本", "zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", str(out_path))

    assert duration == pytest.approx(1.23, abs=0.05)
    assert out_path.exists()


def test_synthesize_multi_chunk_concatenates_and_cleans_up(monkeypatch, tmp_path):
    _install_fake_synthesize(monkeypatch, fail_times=0)
    provider = etp.EdgeTTSProvider(max_chunk_chars=5, sleep_fn=lambda s: None)
    out_path = tmp_path / "out.mp3"

    text = "第一句。第二句。第三句。"  # 触发多块合成（每句约 4 字符，阈值 5）
    duration = provider.synthesize(text, "zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", str(out_path))

    assert duration == pytest.approx(1.23, abs=0.05)
    assert out_path.exists()
    # part 临时文件应已被清理
    leftover_parts = list(tmp_path.glob("out.part*.mp3"))
    assert leftover_parts == []


def test_synthesize_retries_and_recovers(monkeypatch, tmp_path):
    state = _install_fake_synthesize(monkeypatch, fail_times=2)
    sleep_calls = []
    provider = etp.EdgeTTSProvider(max_retries=3, backoff_base=1.0, sleep_fn=sleep_calls.append)
    out_path = tmp_path / "out.mp3"

    duration = provider.synthesize("短文本", "zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", str(out_path))

    assert duration == pytest.approx(1.23, abs=0.05)
    assert state["calls"] == 3
    # 指数退避：第1次失败后等待 1.0*2**0=1.0，第2次失败后等待 1.0*2**1=2.0
    assert sleep_calls == [1.0, 2.0]


def test_synthesize_raises_tts_network_error_after_exhausting_retries(monkeypatch, tmp_path):
    _install_fake_synthesize(monkeypatch, fail_times=10)  # 永远失败
    provider = etp.EdgeTTSProvider(max_retries=3, backoff_base=0.0, sleep_fn=lambda s: None)
    out_path = tmp_path / "out.mp3"

    with pytest.raises(TTSNetworkError):
        provider.synthesize("短文本", "zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", str(out_path))

    # 失败时不应残留半成品输出文件
    assert not out_path.exists()


def test_synthesize_empty_text_raises_value_error(tmp_path):
    provider = etp.EdgeTTSProvider()
    with pytest.raises(ValueError):
        provider.synthesize("   ", "zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", str(tmp_path / "out.mp3"))


def test_synthesize_non_retryable_exception_propagates_without_wrapping(monkeypatch, tmp_path):
    async def fake_synthesize_once(text, voice, rate, pitch, out_path):
        raise ValueError("非法音色参数，不应重试")

    monkeypatch.setattr(etp.EdgeTTSProvider, "_synthesize_once", staticmethod(fake_synthesize_once))
    provider = etp.EdgeTTSProvider(sleep_fn=lambda s: None)

    with pytest.raises(ValueError):
        provider.synthesize("短文本", "bad-voice", "+0%", "+0Hz", str(tmp_path / "out.mp3"))
