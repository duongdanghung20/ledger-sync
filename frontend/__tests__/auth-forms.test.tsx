import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { LoginForm } from "../components/LoginForm";
import { SetPasswordForm } from "../components/SetPasswordForm";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh: vi.fn() }),
}));

afterEach(() => {
  vi.restoreAllMocks();
  push.mockReset();
});

function type(labelText: RegExp, value: string) {
  fireEvent.change(screen.getByLabelText(labelText), { target: { value } });
}

test("login shows a credentials error on 401 and does not navigate", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401 }));
  render(<LoginForm />);
  type(/email/i, "admin@example.com");
  type(/password/i, "wrong-password");
  fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

  expect(await screen.findByRole("alert")).toHaveTextContent(/don't match/i);
  expect(push).not.toHaveBeenCalled();
});

test("login posts credentials and navigates home on success", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
  vi.stubGlobal("fetch", fetchMock);
  render(<LoginForm />);
  type(/email/i, "admin@example.com");
  type(/password/i, "hunter2hunter");
  fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

  await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/auth/login",
    expect.objectContaining({ method: "POST" }),
  );
});

test("set-password rejects a too-short password before calling the server", async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  render(<SetPasswordForm token="abc" />);
  type(/new password/i, "short");
  type(/confirm password/i, "short");
  fireEvent.click(screen.getByRole("button", { name: /set password/i }));

  expect(await screen.findByRole("alert")).toHaveTextContent(/at least 8/i);
  expect(fetchMock).not.toHaveBeenCalled();
});

test("set-password without a token explains the broken link", () => {
  render(<SetPasswordForm token="" />);
  expect(screen.getByRole("alert")).toHaveTextContent(/missing its token/i);
});
