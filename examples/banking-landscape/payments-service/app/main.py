import os

import httpx
from fastapi import APIRouter, FastAPI
from kafka import KafkaProducer
from sqlalchemy import Column, Numeric, String, create_engine
from sqlalchemy.orm import declarative_base

ACCOUNTS_URL = os.getenv("ACCOUNTS_URL", "http://accounts-service:8080")
engine = create_engine("postgresql://payments-db:5432/payments")
Base = declarative_base()
producer = KafkaProducer(bootstrap_servers="kafka:9092")

app = FastAPI(title="Payments Service")
router = APIRouter(prefix="/payment-orders")


class PaymentOrder(Base):
    __tablename__ = "payment_order"
    id = Column(String, primary_key=True)
    debtor_account = Column(String)
    creditor_iban = Column(String)
    amount = Column(Numeric)


@router.post("")
def initiate_payment_order(order: dict):
    balance = httpx.get(f"{ACCOUNTS_URL}/accounts/{order['debtor_account']}/balance").json()
    if balance["available"] < order["amount"]:
        return {"status": "REJECTED", "reason": "insufficient funds"}
    producer.send("payment-orders", order)
    return {"status": "ACCEPTED"}


@router.get("/{order_id}")
def get_payment_order(order_id: str):
    return {"id": order_id}


app.include_router(router)
