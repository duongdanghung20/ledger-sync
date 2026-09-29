import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { UsersAdmin } from "../app/admin/users/UsersAdmin";

// next/link needs no router context here — render it as a plain anchor.
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

const users = [
  { id: "u-admin", email: "admin@example.com", role: "Admin", must_set_password: false, disabled: false },
  { id: "u-book", email: "book@example.com", role: "Bookkeeper", must_set_password: true, disabled: false },
];

function res(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body };
}

function stubFetch(overrides: Record<string, (init?: RequestInit) => unknown> = {}) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const key = `${method} ${url}`;
    if (overrides[key]) return overrides[key](init);
    if (url === "/api/me") return res(200, { id: "u-admin" });
    if (url === "/api/users" && method === "GET") return res(200, users);
    return res(200, {});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.restoreAllMocks();
});

test("lists the org's people and marks the current admin", async () => {
  stubFetch();
  render(<UsersAdmin />);

  expect(await screen.findByText("book@example.com")).toBeInTheDocument();
  expect(screen.getByText(/· you/)).toBeInTheDocument(); // the admin's own row
  expect(screen.getByText("Invitation pending")).toBeInTheDocument(); // book hasn't set a password
});

test("inviting surfaces the one-time set-password link to hand over", async () => {
  stubFetch({
    "POST /api/users/invite": () =>
      res(201, {
        user: { id: "u-new", email: "new@example.com", role: "Bookkeeper", must_set_password: true, disabled: false },
        setup_link: "http://localhost:8080/set-password?token=abc123",
      }),
  });
  render(<UsersAdmin />);
  await screen.findByText("book@example.com");

  fireEvent.change(screen.getByLabelText(/email/i), { target: { value: "new@example.com" } });
  fireEvent.click(screen.getByRole("button", { name: /send invite/i }));

  const link = (await screen.findByLabelText(/set-password link/i)) as HTMLInputElement;
  expect(link.value).toContain("/set-password?token=abc123");
});

test("disabling a person PATCHes disabled=true", async () => {
  const fetchMock = stubFetch({
    "PATCH /api/users/u-book": () => res(200, { ...users[1], disabled: true }),
  });
  render(<UsersAdmin />);
  await screen.findByText("book@example.com");

  fireEvent.click(screen.getByRole("button", { name: /^disable$/i }));

  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/users/u-book",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ disabled: true }) }),
    ),
  );
});
