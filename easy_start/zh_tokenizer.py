"""[MOD] 改造⑤ 中文分词器：jieba 实现 bm25_engine 的 Tokenizer Protocol。

背景：上游 KoreanTokenizer 依赖 kiwipiepy（韩语形态素分析器），对中文制度文档
分词效果差。本项目语料为中文，改用 jieba（纯 Python，零原生依赖）。
接口与 KoreanTokenizer 保持鸭子类型一致（tokenize / tokenize_batch），
可直接注入 BM25Index(tokenizer=...)。
"""

from __future__ import annotations

import jieba

jieba.setLogLevel(60)  # 静音 jieba 的初始化日志


class ChineseTokenizer:
    """jieba 精确模式分词 + 可选停用词过滤。"""

    def __init__(self, stopword_filter: any = None) -> None:
        self.stopword_filter = stopword_filter

    def tokenize(self, text: str) -> list[str]:
        tokens = jieba.lcut(text)
        tokens = [t.strip() for t in tokens if t.strip()]
        if self.stopword_filter is not None:
            tokens = self.stopword_filter.filter(tokens)
        return tokens

    def tokenize_batch(self, texts: list[str]) -> list[list[str]]:
        return [self.tokenize(t) for t in texts]
