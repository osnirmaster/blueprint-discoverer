import { Args, Mutation, Query, Resolver } from "@nestjs/graphql";

@Resolver()
export class CustomerResolver {
  @Query(() => Customer, { name: "customer" })
  async findCustomer(@Args("id") id: string) {
    return { id };
  }

  @Mutation(() => Customer)
  async updateContactDetails(@Args("id") id: string, @Args("email") email: string) {
    return { id, email };
  }
}
