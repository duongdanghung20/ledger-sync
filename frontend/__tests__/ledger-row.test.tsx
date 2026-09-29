import { render, screen } from "@testing-library/react";
import { LedgerRow } from "../components/LedgerRow";

test("renders the label and value", () => {
  render(<LedgerRow label="Database" value="PostgreSQL" />);
  expect(screen.getByText("Database")).toBeInTheDocument();
  expect(screen.getByText("PostgreSQL")).toBeInTheDocument();
});
