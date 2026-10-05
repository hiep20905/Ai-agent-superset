// The published @apache-superset/core npm package predates the `chat`
// contribution API, so it ships no `chat` export. At runtime the import is
// mapped to `window.superset` (webpack externals), which the Superset host
// populates with `chat`. This ambient augmentation adds the compile-time
// types so TypeScript accepts `import { chat } from "@apache-superset/core"`.
import { ComponentType } from "react";

declare module "@apache-superset/core" {
  export namespace chat {
    interface Chat {
      id: string;
      name: string;
      description?: string;
    }
    type DisplayMode = "floating" | "panel";
    function registerChat(
      chat: Chat,
      trigger: ComponentType,
      panel: ComponentType,
    ): { dispose(): void };
    function getChat(): Chat | undefined;
    function open(): void;
    function close(): void;
    function isOpen(): boolean;
    function onDidOpen(listener: () => void): { dispose(): void };
    function onDidClose(listener: () => void): { dispose(): void };
    function getDisplayMode(): DisplayMode;
    function setDisplayMode(mode: DisplayMode): void;
  }
}
