import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";

import { Sidebar } from "./Sidebar";

let mockPathname = "/";

jest.mock("next/navigation", () => ({
  usePathname: () => mockPathname,
  useRouter: () => ({ replace: jest.fn() }),
}));

jest.mock("@/lib/api", () => ({
  clearToken: jest.fn(),
}));

jest.mock("next/link", () => {
  return function MockLink({
    href,
    className,
    children,
  }: {
    href: string;
    className?: string;
    children: ReactNode;
  }) {
    return (
      <a href={href} className={className}>
        {children}
      </a>
    );
  };
});

describe("Sidebar", () => {
  it("renders navigation links", () => {
    mockPathname = "/";
    render(<Sidebar />);

    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New Patient" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Escalations" })).toBeInTheDocument();
  });

  it("marks matching route as active", () => {
    mockPathname = "/escalations";
    render(<Sidebar />);

    const escalationsLink = screen.getByRole("link", { name: "Escalations" });
    expect(escalationsLink.className).toContain("bg-zinc-800");
  });
});
