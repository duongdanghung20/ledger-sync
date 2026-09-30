import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { QuickBooksConnection } from "../app/settings/quickbooks/QuickBooksConnection";

// next/link needs no router context here — render it as a plain anchor.
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

function stubStatus(status: number, body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: status < 400, status, json: async () => body })),
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

test("pending: prompts to connect and points at the authorize endpoint", async () => {
  stubStatus(200, { status: "pending", realm_id: null, connected_at: null });
  render(<QuickBooksConnection />);

  const connect = await screen.findByRole("link", { name: /connect to quickbooks/i });
  expect(connect).toHaveAttribute("href", "/api/qbo/authorize");
});

test("connected: shows the company, when it connected, and a Reconnect link", async () => {
  stubStatus(200, {
    status: "connected",
    realm_id: "4620816365",
    connected_at: "2026-09-20T15:04:00Z",
  });
  render(<QuickBooksConnection />);

  expect(await screen.findByText("4620816365")).toBeInTheDocument();
  expect(screen.getByText(/2026/)).toBeInTheDocument();
  const reconnect = screen.getByRole("link", { name: /^reconnect$/i });
  expect(reconnect).toHaveAttribute("href", "/api/qbo/authorize");
});

test("disconnected: says pushes are blocked and offers a prominent Reconnect", async () => {
  stubStatus(200, { status: "disconnected", realm_id: "4620816365", connected_at: null });
  render(<QuickBooksConnection />);

  expect(await screen.findByText(/pushes are paused/i)).toBeInTheDocument();
  const reconnect = screen.getByRole("link", { name: /reconnect to quickbooks/i });
  expect(reconnect).toHaveAttribute("href", "/api/qbo/authorize");
});

test("403: renders a graceful Admin-only state instead of crashing", async () => {
  stubStatus(403, {});
  render(<QuickBooksConnection />);

  expect(await screen.findByText(/admin only/i)).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /connect|reconnect/i })).not.toBeInTheDocument();
});

test("error banner: the ?error= round-trip shows a retry message and changes nothing", async () => {
  stubStatus(200, { status: "pending", realm_id: null, connected_at: null });
  render(<QuickBooksConnection banner="error" />);

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/didn.t finish/i);
  // The connect prompt is still available underneath the banner.
  expect(await screen.findByRole("link", { name: /connect to quickbooks/i })).toBeInTheDocument();
});
