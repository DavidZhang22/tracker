import { render, screen } from "@testing-library/react";
import { day, checked, dateTime } from "../dates";
import { LinkDate } from "../Components/RowTools";

test.each([
  "2026-09-20T08:30:00Z",
  "2026-03-08T06:59:00Z",
  "2026-03-08T07:01:00Z",
  "2026-09-20T23:30:00-07:00",
])(
  "reused formatters preserve source-day and local timestamp output for %s",
  (value) => {
    const date = new Date(value);
    expect(day(value)).toBe(
      date.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      }),
    );
    expect(checked(value)).toBe(
      date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }),
    );
    expect(dateTime(value)).toBe(
      date.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }),
    );
  },
);

test.each([null, "", "bad source timestamp"])(
  "invalid date %s cannot crash a link row",
  (value) => {
    expect(day(value)).toBe("Date unavailable");
    expect(checked(value)).toBe("Not checked");
    render(
      <LinkDate entry={{ published_at: value, date_precision: "time" }} />,
    );
    expect(screen.getByText("Date unavailable")).toBeInTheDocument();
  },
);
