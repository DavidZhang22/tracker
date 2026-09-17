import "@testing-library/react/dont-cleanup-after-each";
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { TextDecoder, TextEncoder } from "node:util";

globalThis.TextEncoder = TextEncoder;
globalThis.TextDecoder = TextDecoder;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

afterEach(cleanup);
