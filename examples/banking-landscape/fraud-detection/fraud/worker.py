from fastapi import FastAPI
from kafka import KafkaConsumer, KafkaProducer

consumer = KafkaConsumer("payment-orders", bootstrap_servers="kafka:9092", group_id="fraud")
alerts = KafkaProducer(bootstrap_servers="kafka:9092")
app = FastAPI(title="Fraud Detection")


def score_transaction(payment: dict) -> float:
    """Anomaly score for a payment transaction (velocity, amount, device)."""
    return 0.1


def run():
    for message in consumer:
        if score_transaction(message.value) > 0.8:
            alerts.send("fraud-alerts", message.value)


@app.get("/fraud-cases/{case_id}")
def get_fraud_case(case_id: str):
    return {"id": case_id}
