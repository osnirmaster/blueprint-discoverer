import { ApolloClient, InMemoryCache, gql } from "@apollo/client/core";

const client = new ApolloClient({ uri: "http://customer-service:3000/graphql", cache: new InMemoryCache() });

const CUSTOMER_PROFILE = gql`
  query CustomerProfile($id: ID!) {
    customer(id: $id) {
      name
      segment
    }
  }
`;

export const customerProfile = (id: string) => client.query({ query: CUSTOMER_PROFILE, variables: { id } });
