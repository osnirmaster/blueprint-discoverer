from blueprint_discoverer.cartographer.code.extractors.base import FileContext
from blueprint_discoverer.cartographer.code.extractors.java import JavaExtractor
from blueprint_discoverer.cartographer.code.extractors.python import PythonExtractor
from blueprint_discoverer.cartographer.code.extractors.typescript import TypeScriptExtractor


def ctx(path, src):
    return FileContext(repo="r", path=path, source=src.encode())


def test_java_spring_controller_feign_and_kafka():
    src = '''
    package com.bank.x;
    @RestController @RequestMapping("/loans")
    class LoanController {
      @GetMapping("/{id}") Loan get(@PathVariable String id) { return null; }
      @RequestMapping(value = "/{id}/repay", method = RequestMethod.POST) void repay() {}
      @KafkaListener(topics = {"loan-events", "rates"}) void on(String m) { kafkaTemplate.send("loan-audit", m); }
    }
    @FeignClient(name = "pricing-service") interface Pricing { @PostMapping("/quotes") Quote q(); }
    '''
    f = JavaExtractor().extract(ctx("A.java", src))
    assert {(e.method, e.path) for e in f.endpoints} == {("GET", "/loans/{id}"), ("POST", "/loans/{id}/repay")}
    out = {(o.kind.value, o.target) for o in f.outbound}
    assert ("subscribe", "loan-events") in out and ("subscribe", "rates") in out
    assert ("publish", "loan-audit") in out
    assert ("http", "pricing-service") in out
    assert "com.bank.x" in f.packages


def test_typescript_nest_express_axios_kafkajs():
    src = '''
    import axios from "axios";
    const BASE = process.env.CARDS_URL ?? "http://cards-service:8080";
    @Controller("statements") export class S { @Get(":id") one() {} }
    app.post("/api/pay", h);
    axios.get(`${BASE}/cards/${id}`);
    await producer.send({ topic: "card-events", messages: [] });
    await consumer.subscribe({ topics: ["a", "b"] });
    '''
    f = TypeScriptExtractor().extract(ctx("s.ts", src))
    assert {(e.method, e.path) for e in f.endpoints} == {("GET", "/statements/:id"), ("POST", "/api/pay")}
    targets = {(o.kind.value, o.target) for o in f.outbound}
    assert ("http", "http://cards-service:8080/cards/{id}") in targets
    assert ("publish", "card-events") in targets
    assert ("subscribe", "a") in targets and ("subscribe", "b") in targets


def test_python_fastapi_constant_propagation():
    src = '''
import os, httpx
from fastapi import APIRouter
URL = os.getenv("LIMITS_URL", "http://limits-service:9000")
router = APIRouter(prefix="/limits")
producer = KafkaProducer()
@router.get("/{cid}")
def get_limit(cid): return httpx.get(f"{URL}/exposures/{cid}")
def emit(): producer.send("limit-breaches", {})
class Exposure(Base):
    __tablename__ = "exposure"
'''
    f = PythonExtractor().extract(ctx("m.py", src))
    assert [(e.method, e.path) for e in f.endpoints] == [("GET", "/limits/{cid}")]
    targets = {(o.kind.value, o.target, o.detail) for o in f.outbound}
    assert ("http", "http://limits-service:9000/exposures/{cid}", "GET") in targets
    assert ("publish", "limit-breaches", "") in targets
    assert ("database", "", "exposure") in targets
