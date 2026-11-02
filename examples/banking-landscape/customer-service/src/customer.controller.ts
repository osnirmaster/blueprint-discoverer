import { Body, Controller, Get, Param, Post, Put } from "@nestjs/common";
import { Kafka } from "kafkajs";

const producer = new Kafka({ brokers: ["kafka:9092"] }).producer();

@Controller("customers")
export class CustomerController {
  @Get(":customerId")
  async getCustomer(@Param("customerId") customerId: string) {
    return { customerId };
  }

  @Post()
  async onboardCustomer(@Body() party: any) {
    await producer.send({ topic: "customer-events", messages: [{ value: JSON.stringify(party) }] });
    return party;
  }

  @Put(":customerId/kyc")
  async updateKycStatus(@Param("customerId") customerId: string) {
    return { customerId, kyc: "VERIFIED" };
  }
}
