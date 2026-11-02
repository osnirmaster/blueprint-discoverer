import express from "express";
import axios from "axios";

const ACCOUNTS = process.env.ACCOUNTS_URL ?? "http://accounts-service:8080";
const PAYMENTS = process.env.PAYMENTS_URL ?? "http://payments-service:8000";
const CUSTOMERS = "http://customer-service:3000";

const app = express();

app.get("/api/dashboard/:customerId", async (req, res) => {
  const customer = await axios.get(`${CUSTOMERS}/customers/${req.params.customerId}`);
  const balance = await axios.get(`${ACCOUNTS}/accounts/${req.query.accountId}/balance`);
  res.json({ customer: customer.data, balance: balance.data });
});

app.post("/api/transfers", async (req, res) => {
  const result = await axios.post(`${PAYMENTS}/payment-orders`, req.body);
  res.json(result.data);
});

app.listen(4000);
