from types import SimpleNamespace

from blueprint_discoverer.semantic.bian.landscape import load_landscape
from blueprint_discoverer.semantic.deterministic import DeterministicClassifier
from blueprint_discoverer.semantic.llm import LlmClassification, LlmInferencer


class FakeMessages:
    def __init__(self, result):
        self.result, self.calls = result, 0

    def parse(self, **kwargs):
        self.calls += 1
        assert kwargs["output_format"] is LlmClassification
        assert kwargs["fallbacks"] == "default"
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.result, stop_details=None)


def fake_client(result):
    msgs = FakeMessages(result)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def _classify(pipeline, tmp_path, result):
    scans, links = pipeline.load_scans(), pipeline.load_links()
    comp = next(c for s in scans for c in s.components if c.id == "payments-service")
    det = DeterministicClassifier(load_landscape())
    client, msgs = fake_client(result)
    inf = LlmInferencer(load_landscape(), tmp_path, client=client)
    out = inf.classify(comp, links, det.profile(comp, links), det.rank(comp, links))
    return inf, out, msgs, comp, links, det


def llm_result(primary, secondary=(), refs=()):
    return LlmClassification(
        purpose="Processa ordens de pagamento.", objectives=["Validar saldo"], business_capabilities=["Pagamentos"],
        business_functions=["Iniciar ordem"], primary_service_domain_id=primary,
        secondary_service_domain_ids=list(secondary), confidence=0.9, rationale="Endpoints /payment-orders.",
        evidence_refs=list(refs) + ["made:up:1"])


def test_agreeing_llm_raises_confidence_and_is_cached(pipeline, tmp_path):
    inf, out, msgs, comp, links, det = _classify(pipeline, tmp_path, llm_result("payment-order", ["not-a-domain"]))
    assert out.primary_domain.service_domain_id == "payment-order"
    assert out.method == "llm" and not out.review_required
    assert "made:up:1" not in out.primary_domain.evidence  # hallucinated refs are dropped
    assert "dropped unknown secondary" in out.rationale
    again = inf.classify(comp, links, det.profile(comp, links), det.rank(comp, links))
    assert again.method == "llm-cached" and msgs.calls == 1


def test_unknown_domain_falls_back_to_deterministic(pipeline, tmp_path):
    _, out, *_ = _classify(pipeline, tmp_path, llm_result("payments-hub"))
    assert out.primary_domain.service_domain_id == "payment-order"
    assert out.method == "deterministic" and out.review_required


def test_unsupported_domain_requires_review(pipeline, tmp_path):
    _, out, *_ = _classify(pipeline, tmp_path, llm_result("mortgage-loan"))
    assert out.review_required and out.confidence <= 0.6


def test_human_override_wins(pipeline):
    from blueprint_discoverer.semantic.inference import SemanticEngine

    scans, links = pipeline.load_scans(), pipeline.load_links()
    comp = next(c for s in scans for c in s.components if c.id == "customer-service")
    engine = SemanticEngine(pipeline.ws.llm_cache_dir, overrides={"customer-service": "party-lifecycle-management"})
    p = engine.infer(comp, links)
    assert p.primary_domain.service_domain_id == "party-lifecycle-management"
    assert p.method == "human" and p.confidence == 1.0 and not p.review_required
    assert "party-lifecycle-management" not in {c.service_domain_id for c in p.secondary_domains}
