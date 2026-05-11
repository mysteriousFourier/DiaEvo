from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .models import TraceRecord


TOKEN_RE = re.compile(r"[a-zA-Z0-9_+#.-]+|[\u4e00-\u9fff]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "当前",
    "项目",
    "一个",
    "使用",
}


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TOKEN_RE.findall(text.lower()):
        if not match or match in STOPWORDS:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]+", match) and len(match) > 2:
            tokens.append(match)
            tokens.extend(match[index : index + 2] for index in range(len(match) - 1))
        else:
            tokens.append(match)
    return [token for token in tokens if token and token not in STOPWORDS]


def build_vocabulary(tokenized_docs: list[list[str]], max_features: int = 2000) -> dict[str, int]:
    document_frequency: Counter[str] = Counter()
    term_frequency: Counter[str] = Counter()
    for tokens in tokenized_docs:
        term_frequency.update(tokens)
        document_frequency.update(set(tokens))
    ordered = sorted(
        term_frequency,
        key=lambda token: (-document_frequency[token], -term_frequency[token], token),
    )
    return {token: index for index, token in enumerate(ordered[:max_features])}


def compute_idf(tokenized_docs: list[list[str]], vocabulary: dict[str, int]) -> list[float]:
    doc_count = max(1, len(tokenized_docs))
    df = [0] * len(vocabulary)
    for tokens in tokenized_docs:
        for token in set(tokens):
            index = vocabulary.get(token)
            if index is not None:
                df[index] += 1
    return [math.log((1 + doc_count) / (1 + value)) + 1.0 for value in df]


def vectorize_tokens(tokens: list[str], vocabulary: dict[str, int], idf: list[float]) -> list[float]:
    vector = [0.0] * len(vocabulary)
    counts = Counter(tokens)
    if not counts:
        return vector
    max_count = max(counts.values())
    for token, count in counts.items():
        index = vocabulary.get(token)
        if index is None:
            continue
        vector[index] = (count / max_count) * idf[index]
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return vector


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def top_terms(vector: list[float], vocabulary: dict[str, int], limit: int = 8) -> list[str]:
    inverse = {index: token for token, index in vocabulary.items()}
    ranked = sorted(enumerate(vector), key=lambda item: item[1], reverse=True)
    return [inverse[index] for index, value in ranked[:limit] if value > 0]


def command_ngrams(commands: list[str], n: int = 2) -> list[str]:
    tokens = []
    for command in commands:
        command_tokens = tokenize(command)
        tokens.extend(command_tokens)
    if n <= 1:
        return tokens
    return [" ".join(tokens[index : index + n]) for index in range(max(0, len(tokens) - n + 1))]


@dataclass(slots=True)
class FeatureStore:
    documents: list[str]
    tokens: list[list[str]]
    vocabulary: dict[str, int]
    idf: list[float]
    vectors: list[list[float]]

    @classmethod
    def from_documents(cls, documents: list[str], max_features: int = 2000) -> "FeatureStore":
        tokenized = [tokenize(document) for document in documents]
        vocabulary = build_vocabulary(tokenized, max_features=max_features)
        idf = compute_idf(tokenized, vocabulary)
        vectors = [vectorize_tokens(tokens, vocabulary, idf) for tokens in tokenized]
        return cls(documents=documents, tokens=tokenized, vocabulary=vocabulary, idf=idf, vectors=vectors)

    @classmethod
    def from_traces(cls, traces: list[TraceRecord], max_features: int = 2000) -> "FeatureStore":
        return cls.from_documents([trace.document for trace in traces], max_features=max_features)

    def vectorize(self, text: str) -> list[float]:
        return vectorize_tokens(tokenize(text), self.vocabulary, self.idf)

    def nearest(self, text: str, limit: int = 5) -> list[tuple[int, float]]:
        query = self.vectorize(text)
        ranked = sorted(
            ((index, cosine(query, vector)) for index, vector in enumerate(self.vectors)),
            key=lambda item: item[1],
            reverse=True,
        )
        return ranked[:limit]
