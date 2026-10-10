const CHANNEL = "trackify:session-change";
const SENDER = `${Date.now()}:${Math.random()}`;

export function notifySessionChange() {
  try {
    const channel = new BroadcastChannel(CHANNEL);
    channel.postMessage({ type: "changed", sender: SENDER });
    channel.close();
    return;
  } catch {
    // Older or restricted browsers can use a transient, non-secret signal.
  }
  try {
    localStorage.setItem(CHANNEL, `${Date.now()}:${Math.random()}`);
    localStorage.removeItem(CHANNEL);
  } catch {
    // Session checks and HTTP authorization still apply without browser storage.
  }
}

export function watchSessionChanges(onChange) {
  let channel;
  try {
    channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = ({ data }) => {
      if (data?.type === "changed" && data.sender !== SENDER) onChange();
    };
  } catch {
    channel = null;
  }
  const changed = (event) => {
    if (event.key === CHANNEL && event.newValue !== null) onChange();
  };
  window.addEventListener("storage", changed);
  return () => {
    channel?.close();
    window.removeEventListener("storage", changed);
  };
}
