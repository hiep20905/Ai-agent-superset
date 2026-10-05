import { chat } from "@apache-superset/core";
import ChatTrigger from "./ChatTrigger";
import ChatPanel from "./ChatPanel";

// Register the chat contribution. The host (Superset) owns where the trigger
// bubble and panel are mounted; we only supply the two React components.
chat.registerChat(
  { id: "demo.hospital-chat", name: "Hospital Chat" },
  ChatTrigger,
  ChatPanel,
);
