from diaevo.features import FeatureStore, cosine, tokenize


def test_tokenize_keeps_chinese_bigrams_and_ascii_terms():
    tokens = tokenize("给 Python 项目生成测试修复 skill")
    assert "python" in tokens
    assert "skill" in tokens
    assert "测试" in tokens


def test_feature_store_nearest_prefers_related_document():
    store = FeatureStore.from_documents(["pytest failure repair", "frontend screenshot check"])
    nearest = store.nearest("fix pytest test failure", limit=1)
    assert nearest[0][0] == 0
    assert cosine(store.vectorize("pytest"), store.vectorize("pytest failure")) > 0
