import { createSearchHandler } from "./workerHandler.js";

const handle = createSearchHandler((message) => self.postMessage(message));
self.onmessage = (event) => handle(event.data);
